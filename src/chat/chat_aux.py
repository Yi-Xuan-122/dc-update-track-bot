from typing import Set, Optional, Dict, Any, List, Callable
import datetime
import logging
import discord
import random
from src.config import IMG_VIEW, UTC_ZONE
from src.summary.summary_aux import convert_to_local_timezone

log = logging.getLogger(__name__)

# 定义回调函数类型别名
PostProcessing = Callable[[str, Optional[List[str]], Optional[Dict[str, Any]]], Any]

class ChatHistoryTemplate:
    def __init__(self, bot_user: discord.User, admin_ids: Set[int] = None):
        """
        初始化聊天记录处理器。
        Seed 在此处生成，确保整个会话（无论分多少页）使用同一个随机种子。
        """
        self.bot_user = bot_user
        self.admin_ids = admin_ids or set()
        
        # --- 状态持久化 ---
        self.seed: int = random.randint(100000, 999999)
        
        # Key: MessageID -> Value: {'floor': int, 'preview': str, 'tag': str}
        self.message_id_map: Dict[int, Dict[str, Any]] = {} 
        
        self.seen_members: Dict[int, str] = {}     # 存储成员ID -> 唯一化昵称
        self.name_usage_count: Dict[str, int] = {} # 存储重名计数
        
        # --- 楼层与分页控制 ---
        self.total_processed_count = 0             # 全局已处理消息数 (用于计算楼层)
        self.last_floor_message_id: Optional[int] = None # 当前最久远的一条消息ID (用于翻页)
        
        # --- 时间控制 ---
        self.last_timestamp: Optional[datetime.datetime] = None

    def _get_identity_tag(self, author: discord.User | discord.Member) -> str:
        """生成用户身份标签，同时保证同一用户在不同批次中昵称一致"""
        author_id = author.id
        
        if author_id not in self.seen_members:
            raw_name = author.display_name
            if raw_name in self.name_usage_count:
                self.name_usage_count[raw_name] += 1
                unique_name = f"{raw_name} ({self.name_usage_count[raw_name]})"
            else:
                self.name_usage_count[raw_name] = 1
                unique_name = raw_name
            self.seen_members[author_id] = unique_name

        nickname = self.seen_members[author_id]
        
        identity_type = "User"
        auth_attr = ""

        if author_id == self.bot_user.id:
            identity_type = "System-Self"
            auth_attr = f' Auth="{self.seed}"'
        elif author_id in self.admin_ids:
            identity_type = "Master"
            auth_attr = f' Auth="{self.seed}"'
        elif author.bot:
            identity_type = "Bot"

        return f'display_name="{nickname}" role="{identity_type}"{auth_attr}'

    async def parse_messages(
        self, 
        messages: List[discord.Message], 
        post_processing_callback: PostProcessing,
        main_prompt: Any = None,
        members_ids: Set[int] = None
    ) -> Any:
        """
        核心处理函数。
        :param messages: 本次获取的消息列表 (Discord API 默认返回顺序：Newest -> Oldest)
        """
        if not messages:
            return main_prompt

        # 记录本批次中最久远的消息ID
        self.last_floor_message_id = messages[-1].id
        batch_size = len(messages)
        is_first_batch = (self.total_processed_count == 0)

        for i, msg in enumerate(messages):
            # 楼层计算：
            # Newest (i=0) -> floor = total + 1 + 0 = total + 1
            # Oldest (i=99) -> floor = total + 1 + 99 = total + 100
            # 结果：最新消息是 Floor X，旧消息是 Floor X+N
            floor_num = self.total_processed_count + 1 + i
            
            clean_text = msg.content.replace("\n", " ").strip()
            if len(clean_text) > 30:
                short_text = clean_text[:30] + "..."
            elif not clean_text and msg.attachments:
                short_text = "[图片/文件]"
            elif not clean_text:
                short_text = "[无内容]"
            else:
                short_text = clean_text
            
            tag = self._get_identity_tag(msg.author)
            
            # 存入 Map，供回复引用查询
            self.message_id_map[msg.id] = {
                "floor": floor_num,
                "preview": short_text,
                "tag": tag
            }

        # 第二轮遍历：生成 Prompt (倒序: Oldest -> Newest)
        chrono_messages = list(reversed(messages))
        start_str = ""
        if is_first_batch:
            start_str = f"[本次全部的System Seed为: {self.seed}，请注意核对。]\n\n[System Seed:{self.seed}]: ---聊天记录开始---\n"

        for i, items in enumerate(chrono_messages):
            if members_ids and (items.author.id not in members_ids):
                continue
            
            # 直接从 map 获取该消息的属性，避免重复计算
            current_meta = self.message_id_map.get(items.id)
            if not current_meta:
                continue # 理论上不会发生

            current_floor = current_meta['floor']
            tag_content = current_meta['tag']

            # --- 时间流逝逻辑 ---
            pass_time_str = ""
            current_time = items.created_at
            
            if self.last_timestamp is None:
                pass_time_str = f"\n[时间:{convert_to_local_timezone(current_time)}]\n"
            else:
                time_diff = current_time - self.last_timestamp
                if time_diff.total_seconds() > 300:
                    minutes_passed = int(time_diff.total_seconds() // 60)
                    pass_time_str = f"\n[System Seed:{self.seed}]: ---(过了{minutes_passed}分钟后，当前时间:{convert_to_local_timezone(current_time)})\n"
            
            self.last_timestamp = current_time

            # --- 处理回复引用 (Reply Logic) ---
            reply_str = ""
            if items.reference and items.reference.message_id:
                ref_id = items.reference.message_id
                target_meta = self.message_id_map.get(ref_id)
                
                if target_meta:
                    # 找到了目标楼层 (在当前批次或之前批次)
                    ref_floor = target_meta['floor']
                    ref_preview = target_meta['preview']
                    ref_tag = target_meta['tag']
                    # 格式：[Replying to #15 <Tag>: "preview..."]
                    reply_str = f"[Replying to Floor:#{ref_floor} <{ref_tag}>: \"{ref_preview}\"]\n"
                else:
                    # 找不到 (可能是太久以前未加载，或被删除)
                    reply_str = f"[Replying to Unknown Floor (MsgID:{ref_id})]\n"

            # --- 消息正文 ---
            human_text = items.content.replace("】", " ] ")
            img_urls: List[str] = [att.url for att in items.attachments] if IMG_VIEW else []
            
            # 最终格式: [#{Floor}] {Reply} 【<Tag> say: "Content"】
            msg_content = f"[#{current_floor}] {reply_str}【<{tag_content}> say : \"{human_text}\" 】\n"
            
            final_str = pass_time_str + msg_content
            if start_str and i == 0 and is_first_batch:
                final_str = start_str + final_str

            # 透传回调
            main_prompt = await post_processing_callback(final_str, img_urls, main_prompt)

        # 更新全局计数
        self.total_processed_count += batch_size
        
        return main_prompt

    async def finalize_prompt(self, main_prompt: Any, post_processing_callback: PostProcessing) -> Any:
        """
        结束对话记录封装。
        """
        current_time_str = convert_to_local_timezone(datetime.datetime.now(UTC_ZONE)) if self.last_timestamp is None else convert_to_local_timezone(self.last_timestamp)
        
        member_list_parts = [
            f"\n[System Seed:{self.seed}:当前记录结束时间:{current_time_str}]",
            f"[System Seed:{self.seed}]: ---聊天记录结束---",
            f"[Total Floors: {self.total_processed_count}]",
            "<members_list>"
        ]
        
        for m_id, m_name in self.seen_members.items():
            member_list_parts.append(f"<display_name=\"{m_name}\">:\"{m_id}\"")
        member_list_parts.append("</members_list>")
        
        end_str = "\n".join(member_list_parts)
        main_prompt = await post_processing_callback(end_str, None, main_prompt)
        
        if isinstance(main_prompt, dict):
            main_prompt["_system_seed"] = self.seed
            main_prompt["_last_floor_msg_id"] = self.last_floor_message_id

        return main_prompt