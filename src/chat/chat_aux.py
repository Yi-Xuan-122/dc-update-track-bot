from typing import Set, Optional, Dict, Any, List, Callable, Tuple, Iterable
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

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> str:
        if text is None:
            return ""
        return str(text).replace("\r\n", "\n").replace("\r", "\n").replace("】", " ] ").replace('"', "'").strip()

    @staticmethod
    def _truncate_preview(text: str, limit: int = 30) -> str:
        return text[:limit] + "..." if len(text) > limit else text

    @staticmethod
    def _looks_like_image_url(url: Optional[str]) -> bool:
        if not url:
            return False
        normalized_url = url.split("?", 1)[0].lower()
        return normalized_url.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"))

    @classmethod
    def _is_visual_attachment(cls, attachment: Any) -> bool:
        content_type = (getattr(attachment, "content_type", None) or "").lower()
        if content_type.startswith("image/"):
            return True
        if content_type:
            return False
        url = getattr(attachment, "url", None) or getattr(attachment, "proxy_url", None)
        return cls._looks_like_image_url(url)

    def _collect_attachment_context(self, attachments: Iterable[Any], context_label: str, image_counter: int) -> Tuple[List[str], List[str], int]:
        text_lines: List[str] = []
        image_urls: List[str] = []
        for index, attachment in enumerate(attachments or [], start=1):
            filename = getattr(attachment, "filename", None) or f"unnamed_{index}"
            content_type = getattr(attachment, "content_type", None) or "unknown"
            url = getattr(attachment, "url", None) or getattr(attachment, "proxy_url", None)
            if self._is_visual_attachment(attachment):
                image_counter += 1
                text_lines.append(f"[{context_label}图片#{image_counter}: {filename}]")
                if IMG_VIEW and url:
                    image_urls.append(url)
            else:
                type_suffix = "" if content_type == "unknown" else f" ({content_type})"
                text_lines.append(f"[{context_label}文件: {filename}{type_suffix}]")
        return text_lines, image_urls, image_counter

    def _collect_embed_context(self, embeds: Iterable[discord.Embed], context_label: str, image_counter: int) -> Tuple[List[str], List[str], int]:
        text_lines: List[str] = []
        image_urls: List[str] = []
        for index, embed in enumerate(embeds or [], start=1):
            embed_details: List[str] = [f"[{context_label}#{index}"]

            if getattr(embed, "type", None):
                embed_details.append(f"类型:{self._sanitize_text(embed.type)}")
            if getattr(embed, "title", None):
                embed_details.append(f"标题:{self._sanitize_text(embed.title)}")
            if getattr(embed, "description", None):
                embed_details.append(f"描述:{self._sanitize_text(embed.description)}")

            author_name = getattr(getattr(embed, "author", None), "name", None)
            if author_name:
                embed_details.append(f"作者:{self._sanitize_text(author_name)}")

            provider_name = getattr(getattr(embed, "provider", None), "name", None)
            if provider_name:
                embed_details.append(f"来源:{self._sanitize_text(provider_name)}")

            if getattr(embed, "url", None):
                embed_details.append(f"链接:{embed.url}")

            field_chunks = []
            for field in getattr(embed, "fields", []):
                field_name = self._sanitize_text(getattr(field, "name", ""))
                field_value = self._sanitize_text(getattr(field, "value", ""))
                if field_name or field_value:
                    field_chunks.append(f"{field_name}={field_value}".strip("="))
            if field_chunks:
                embed_details.append("字段:" + " | ".join(field_chunks))

            footer_text = getattr(getattr(embed, "footer", None), "text", None)
            if footer_text:
                embed_details.append(f"页脚:{self._sanitize_text(footer_text)}")

            text_lines.append("; ".join(embed_details) + "]")

            image_candidates = [
                ("主图", getattr(getattr(embed, "image", None), "url", None)),
                ("缩略图", getattr(getattr(embed, "thumbnail", None), "url", None)),
            ]
            for image_kind, image_url in image_candidates:
                if image_url:
                    image_counter += 1
                    text_lines.append(f"[{context_label}#{index} {image_kind}图片#{image_counter}]")
                    if IMG_VIEW:
                        image_urls.append(image_url)
        return text_lines, image_urls, image_counter

    def _collect_forwarded_context(self, snapshots: Iterable[Any], image_counter: int) -> Tuple[List[str], List[str], int]:
        text_lines: List[str] = []
        image_urls: List[str] = []
        for index, snapshot in enumerate(snapshots or [], start=1):
            source_message = getattr(snapshot, "message", snapshot)
            forwarded_details = [f"[转发消息#{index}"]

            forwarded_author = getattr(getattr(source_message, "author", None), "display_name", None)
            if not forwarded_author:
                forwarded_author = getattr(getattr(source_message, "author", None), "name", None)
            if forwarded_author:
                forwarded_details.append(f"作者:{self._sanitize_text(forwarded_author)}")

            forwarded_content = self._sanitize_text(
                getattr(source_message, "content", None) or getattr(snapshot, "content", None)
            )
            if forwarded_content:
                forwarded_details.append(f"内容:{forwarded_content}")

            text_lines.append("; ".join(forwarded_details) + "]")

            forwarded_attachments = list(
                getattr(source_message, "attachments", None) or getattr(snapshot, "attachments", []) or []
            )
            attachment_lines, attachment_urls, image_counter = self._collect_attachment_context(
                attachments=forwarded_attachments,
                context_label=f"转发#{index}附件",
                image_counter=image_counter
            )
            text_lines.extend(attachment_lines)
            image_urls.extend(attachment_urls)

            forwarded_embeds = list(
                getattr(source_message, "embeds", None) or getattr(snapshot, "embeds", []) or []
            )
            embed_lines, embed_urls, image_counter = self._collect_embed_context(
                embeds=forwarded_embeds,
                context_label=f"转发#{index}Embed",
                image_counter=image_counter
            )
            text_lines.extend(embed_lines)
            image_urls.extend(embed_urls)

        return text_lines, image_urls, image_counter

    def _build_preview(self, msg: discord.Message) -> str:
        clean_text = self._sanitize_text(msg.content).replace("\n", " ")

        if not clean_text and getattr(msg, "embeds", None):
            first_embed = msg.embeds[0]
            embed_preview = self._sanitize_text(
                " ".join(filter(None, [getattr(first_embed, "title", None), getattr(first_embed, "description", None)]))
            ).replace("\n", " ")
            if embed_preview:
                clean_text = f"[Embed] {embed_preview}"

        markers: List[str] = []
        if getattr(msg, "attachments", None):
            markers.append("[图片/文件]")
        if getattr(msg, "embeds", None):
            markers.append("[Embed]")
        if getattr(msg, "snapshots", None):
            markers.append("[转发消息]")

        preview_text = clean_text
        if markers:
            preview_text = f"{preview_text} {' '.join(markers)}".strip()

        return self._truncate_preview(preview_text or "[无内容]")

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

        # === 第一遍循环：构建索引和预览 ===
        for i, msg in enumerate(messages):
            # 楼层计算
            floor_num = self.total_processed_count + 1 + i
            
            short_text = self._build_preview(msg)
            
            tag = self._get_identity_tag(msg.author)
            
            # 存入 Map
            self.message_id_map[msg.id] = {
                "floor": floor_num,
                "preview": short_text,
                "tag": tag
            }

        # === 第二遍循环：生成 Prompt (倒序) ===
        chrono_messages = list(reversed(messages))
        start_str = ""
        if is_first_batch:
            start_str = f"[本次全部的System Seed为: {self.seed}，请注意核对。]\n\n[System Seed:{self.seed}]: ---聊天记录开始---\n"

        for i, items in enumerate(chrono_messages):
            if members_ids and (items.author.id not in members_ids):
                continue
            
            current_meta = self.message_id_map.get(items.id)
            if not current_meta:
                continue 

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

            # --- 回复引用逻辑 ---
            reply_str = ""
            if items.reference and items.reference.message_id:
                ref_id = items.reference.message_id
                target_meta = self.message_id_map.get(ref_id)
                if target_meta:
                    ref_floor = target_meta['floor']
                    ref_preview = target_meta['preview']
                    ref_tag = target_meta['tag']
                    reply_str = f"[Replying to Floor:[#{ref_floor}] <{ref_tag}>: \"{ref_preview}\"]\n"
                else:
                    reply_str = f"[Replying to Unknown Floor (MsgID:{ref_id})]\n"

            # --- 消息正文与附件 ---
            human_text = self._sanitize_text(items.content)
            img_urls: List[str] = []
            image_counter = 0
            supplemental_lines: List[str] = []

            attachment_lines, attachment_urls, image_counter = self._collect_attachment_context(
                attachments=items.attachments,
                context_label="消息附件",
                image_counter=image_counter
            )
            supplemental_lines.extend(attachment_lines)
            img_urls.extend(attachment_urls)

            embed_lines, embed_urls, image_counter = self._collect_embed_context(
                embeds=getattr(items, "embeds", []),
                context_label="消息Embed",
                image_counter=image_counter
            )
            supplemental_lines.extend(embed_lines)
            img_urls.extend(embed_urls)

            if hasattr(items, 'snapshots') and items.snapshots:
                forwarded_lines, forwarded_urls, image_counter = self._collect_forwarded_context(
                    snapshots=items.snapshots,
                    image_counter=image_counter
                )
                supplemental_lines.extend(forwarded_lines)
                img_urls.extend(forwarded_urls)

            if supplemental_lines:
                if human_text:
                    human_text += "\n" + "\n".join(supplemental_lines)
                else:
                    human_text = "\n".join(supplemental_lines)

            if not human_text:
                human_text = "[无文本内容]"
            
            # 最终格式
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