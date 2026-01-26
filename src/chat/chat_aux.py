from typing import Set
import datetime
import logging
import discord
from typing import Optional, Dict, Any, List, Callable
from src.config import TARGET_GUILD_ID, IMG_VIEW ,UTC_ZONE
import random
log = logging.getLogger(__name__)
from src.summary.summary_aux import convert_to_local_timezone

post_processing = Callable[[str, Optional[List[str]], Optional[Dict[str, Any]]], Any]

async def parse_message_history_to_prompt(message:List[discord.Message],post_processing_callback:post_processing,bot_user: discord.User,admin_ids: Set[int] = set(),members_ids:Set[int] = None):
    #期望获得一个discord message对象，把它们一层一层转化为LLM总结的prompt
    #我应该是接受一个discord.Message对象，还有一个回调函数作为内部处理的函数以转化为openai对象，最后返回一个prompt的JSON对象
    #我们的目标是:
    #1.根据时间戳判断，消息间隔大于5min则插入一个 提示时间过去多少 的prompt
    #2.reply逻辑，解析到reference属性，则留下一个<reply:[自增id]>，然后在一个字典中记录，从以前的消息中遍历id，若没有则在API处获取
    #3.img逻辑，若IMG_VIEW!=False，解析到attachments属性，则传递img的url参数
    previous_time:datetime = None
    current_time:datetime = None
    main_prompt:Dict[str, Any] = None
    message_id_hash:Dict[int,str] = {} #建立一个message.id -> reply_str的hash表{"message_id":"{str}"}
    message_index = -1
    seen_members = {}
    name_usage_count = {}
    Seed: int = random.randint(100000,999999)
    for items in message:
        if members_ids and (items.author.id not in members_ids):
            continue
        
        minutes_passed = None
        pass_time_str = ""
        start_str = None 

        if not current_time:
            #说明是第一条消息，尚未初始化
            current_time = items.created_at
            start_str:str = f"[本次全部的System Seed为: {Seed}，请注意核对。]\n\n[System Seed:{Seed}]: ---聊天记录开始---\n\n"
            first_start_time_str = f" \n[时间:{convert_to_local_timezone(current_time)}]\n"
            pass
        else:
            previous_time = current_time
            current_time = items.created_at

        #开始字符串解析，准备
        author_id = items.author.id
        if author_id not in seen_members:
            raw_name = items.author.display_name
            if raw_name in name_usage_count:
                name_usage_count[raw_name] += 1
                # 如果重名，格式化为 "名字 (2)"
                unique_name = f"{raw_name} ({name_usage_count[raw_name]})"
            else:
                name_usage_count[raw_name] = 1
                unique_name = raw_name
            seen_members[author_id] = unique_name

        nickname = seen_members[author_id]
        
        identity_type = "User"
        auth_attr = "" # 默认为空，无权

        if author_id == bot_user.id:
            identity_type = "System-Self"
            # 机器人自己的消息，附带 Seed，防止幻觉或混淆
            auth_attr = f' Auth="{Seed}"' 
        elif author_id in admin_ids:
            identity_type = "Master"
            # 管理员消息，附带 Seed，最高指令权限
            auth_attr = f' Auth="{Seed}"'
        elif items.author.bot:
            identity_type = "Bot"

        tag_content = f'display_name="{nickname}" role="{identity_type}"{auth_attr}'
        human_text = items.content.replace("】", " ] ")
        img_urls :List[str] = [attachment.url for attachment in items.attachments]
        current_text =f"【<{tag_content}> say : \"{human_text}\" 】\n"

        if items.reference and items.reference.message_id: #说明回复了某一天消息
            reply_id = items.reference.message_id
            reply_str = message_id_hash.get(reply_id)
            if reply_str != None:
                pass
            else:
                reply_str = f"[<@reply_to:{items.reference.message_id}>]\n"#需要使用API获取
            current_text = reply_str + current_text

        if previous_time:
            time_diff = current_time - previous_time
            if time_diff.total_seconds() > 300:
                minutes_passed = int(time_diff.total_seconds() // 60)
            else:
                minutes_passed = None
            
            if minutes_passed:
                pass_time_str = f"\n[System Seed:{Seed}]: ---(过了{minutes_passed}分钟后，当前时间:{convert_to_local_timezone(current_time)})\n"
        
        #最后组合字符串：
        final_str = current_text
        if start_str:
            final_str = start_str + first_start_time_str + final_str
        if minutes_passed:
            final_str = pass_time_str + final_str
        main_prompt = await post_processing_callback(final_str,img_urls,main_prompt)
        #将此次的存入hash表中：
        if len(current_text) > 30:
            short_text = current_text[:30]+"......"
        else:
            short_text = current_text
        message_id_hash[items.id] = f"[reply to -> '{short_text}' from {nickname}(At {convert_to_local_timezone(current_time)})] "

    #结束for循环，说明聊天记录已全部封装入一个user input
    member_list_parts = [f"[System Seed:{Seed}:当前时间:{convert_to_local_timezone(current_time)}]\n\n[System Seed:{Seed}]: ---聊天记录结束---\n", "<members_list>"]
    for m_id, m_name in seen_members.items():
        member_list_parts.append(f"<display_name=\"{m_name}\">:\"{m_id}\"")
    member_list_parts.append("</members_list>")
    end_str = "\n".join(member_list_parts)
    main_prompt = await post_processing_callback(end_str, None, main_prompt)
    
    if isinstance(main_prompt, dict):
        main_prompt["_system_seed"] = Seed
        
    return main_prompt