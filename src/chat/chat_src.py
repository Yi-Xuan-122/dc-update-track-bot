import discord
from discord.ext import commands
import logging
import json
import re
from src.chat.chat_env import system_prompt , SYSTEM_PROMPT,CUSTOM_PROMPT_1
from src.config import ADMIN_IDS, GEMINI_TOOL_CALL
from src.chat.gemini_format import gemini_format_callback
from src.config import LLM_FORMAT , LLM_ALLOW_CHANNELS ,ADMIN_IDS
from src.chat.chat_aux import ChatHistoryTemplate
from src.summary.summary_aux import openai_format
from src.llm import LLM, smart_split_message
from src.summary.summary_aux import RateLimitingScheduler
import time
import asyncio
from src.chat.tool_src import tool_manager

class LLM_Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.llm = LLM()
        self.scheduler = RateLimitingScheduler(bot)
        self.log = logging.getLogger("LLM_Chat")
        tool_manager.set_bot_instance(bot)
    
    def cog_load(self):
            logging.debug("LLM Chat is starting.")
            self.scheduler_task = self.bot.loop.create_task(self.scheduler.run())
            self.cache_task = self.bot.loop.create_task(self.scheduler.queue_cache_event())
    
    def cog_unload(self):
        logging.debug("LLM Chat is being cancelled.")
        if self.scheduler_task:
            self.scheduler_task.cancel()
        if self.cache_task:
            self.cache_task.cancel()

    def is_channel_allowed(self, channel):
        if channel.id in LLM_ALLOW_CHANNELS:
            return True
        if hasattr(channel, "parent_id") and channel.parent_id in LLM_ALLOW_CHANNELS:
            return True
        return False

    def is_triggered(self, message: discord.Message) -> bool:
        if isinstance(message.channel, discord.DMChannel):
            return True
        if self.bot.user in message.mentions:
            return True
        if message.reference and message.reference.cached_message:
            if message.reference.cached_message.author.id == self.bot.user.id:
                return True
        elif message.reference and message.mentions:
            pass
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        if tool_manager.is_user_blacklisted(message.author.id):
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        if is_dm:
            if message.author.id not in ADMIN_IDS:
                return
        else:
            if not self.is_channel_allowed(message.channel):
                return
            if not self.is_triggered(message):
                return

        max_tool_rounds = 16
        current_round = 0

        try:
            async with message.channel.typing():
                history_messages = [
                    msg async for msg in message.channel.history(limit=100)
                ]
                
                processor = ChatHistoryTemplate(bot_user=self.bot.user,admin_ids=ADMIN_IDS)
                
                if LLM_FORMAT == "gemini":
                    final_data_step_1 = await processor.parse_messages(
                        post_processing_callback=gemini_format_callback,
                        messages=history_messages
                    )
                    final_data = await processor.finalize_prompt(
                        main_prompt=final_data_step_1,
                        post_processing_callback=gemini_format_callback
                    )
                    current_seed = final_data.get("_system_seed")
                    prompt_suffix = f"\n[System Seed : {current_seed}]。正常回复结束时必须使用 <||reply_end||> 结尾。"
                    payload_list = final_data.get("contents", [])
                    payload_list.insert(0, {
                        "systemInstruction": {
                            "parts": [{"text": SYSTEM_PROMPT + prompt_suffix}]
                        }
                    })
                    payload_list.append({
                        "role": 'model',
                        "parts": [ { "text": CUSTOM_PROMPT_1 } ]
                    })
                else:
                    final_data_step1 = processor.parse_messages(
                        messages=history_messages,
                        post_processing_callback=openai_format
                    )
                    final_data = await processor.finalize_prompt(
                        main_prompt=final_data_step_1,
                        post_processing_callback=openai_format
                    )
                    current_seed = final_data.get("_system_seed")
                    prompt_suffix = f"\n[System Seed : {current_seed}]。当需要调用工具时，输出工具标签；当不需要调用工具或工具使用完毕准备回答用户时，输出正文并在结尾加上 <||reply_end||>。"
                    payload_list = final_data.get("messages", [])
                    payload_list.insert(0, {
                        "role": "system",
                        "content": SYSTEM_PROMPT + prompt_suffix
                    })

                start_ts = time.time()

                while current_round < max_tool_rounds:
                    current_round += 1

                    llm_input_json = json.dumps(payload_list, ensure_ascii=False)
                    llm_input_json_debug = truncate_base64_data(llm_input_json)
                    self.log.debug(f"LLM Payload:\n{llm_input_json_debug}")

                    llm_result = await self.llm.llm_call(
                        llm_input_json,
                        include_tools=True
                    )
                    
                    raw_data = llm_result.get("raw", {})
                    self.log.debug(f"LLM Result Type: {llm_result.get('type')}")
                    self.log.debug(f"Raw JSON Response: {json.dumps(raw_data, ensure_ascii=False)}")
                    result_type = llm_result.get("type")
                    text_content = llm_result.get("text", "")
                    
                   # =========================================================
                    #  Custom Tool Call 解析
                    found_custom_tool = False
                    custom_tool_data = {}
                    parse_error_msg = None
                    
                    if GEMINI_TOOL_CALL == "custom" and result_type == "text":
                        # 匹配标签内容
                        pattern = r'<custom_tool_call>(.*?)(?:</custom_tool_call>|$)'
                        match = re.search(pattern, text_content, re.DOTALL)
                        
                        if match:
                            found_custom_tool = True
                            tool_content_str = match.group(1).strip()
                            self.log.info(f"Custom Tool String Detected: {tool_content_str}")
                            
                            try:
                                # 1. 提取工具名称
                                name_match = re.search(r'name\s*=\s*"([^"]+)"', tool_content_str)
                                if not name_match:
                                    raise ValueError("解析失败：未在标签内找到有效的 name=\"工具名\" 参数。")
                                
                                func_name = name_match.group(1)
                                
                                # 2. 提取参数对并尝试转化为 JSON 兼容格式
                                params_part = re.sub(r'name\s*=\s*"[^"]+"', '', tool_content_str).strip()
                                params_part = params_part.strip(',').strip()
                                
                                if not params_part:
                                    args = {}
                                else:
                                    # 此时 params_part 可能是: "query"="xxx", "engines"=["google"]
                                    # 我们利用正则将 = 替换为 :
                                    # 匹配 "key" = 
                                    json_ready = re.sub(r'(\"[^\"]+\")\s*=\s*', r'\1: ', params_part)
                                    # 包装成 JSON 对象
                                    try:
                                        args = json.loads("{" + json_ready + "}")
                                    except json.JSONDecodeError:
                                        # 如果 JSON 解析失败，说明存在非标准格式（如未引号的字符串或格式混乱）
                                        raise ValueError("参数语法错误：请确保参数符合 \"key\"=value 格式，且字符串和数组符合 JSON 标准。")

                                custom_tool_data = {"name": func_name, "args": args}

                            except Exception as e:
                                found_custom_tool = True # 标记为工具调用，但我们需要处理错误
                                parse_error_msg = str(e)
                                self.log.error(f"Custom Tool Parse Error: {parse_error_msg}")

                    # =========================================================

                    # ---------- 分发逻辑 ----------
                    context_kwargs = {}
                    if message.guild:
                        context_kwargs['_context_guild_id'] = message.guild.id
                    if result_type == "tool_call":
                        func_name = llm_result["name"]
                        func_args = llm_result["args"]
                        original_parts = llm_result.get("parts_trace")
                        
                        self.log.info(f"Native Tool Call → {func_name}")
                        
                        tool_result = await tool_manager.handle_tool_call(func_name, func_args,**context_kwargs)
                        
                        payload_list.append({"role": "model", "parts": original_parts})
                        payload_list.append({
                            "role": "user",
                            "parts": [{
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {"content": tool_result}
                                }
                            }]
                        })
                        continue

                    elif found_custom_tool:
                        # 补全历史记录（model 角色）
                        model_history_text = text_content if "</custom_tool_call>" in text_content else text_content + "</custom_tool_call>"
                        payload_list.append({"role": "model", "parts": [{"text": model_history_text}]})

                        if parse_error_msg:
                            # 解析失败：反馈给模型
                            error_feedback = f"""
                                [System seed:{current_seed}]: ❌ 工具调用语法解析失败。
                                原因: {parse_error_msg}
                                提示: 请严格遵循 "key"=value 格式。字符串须加双引号，数组须使用标准 JSON 的方括号 [] 格式。
                                格式范例 (含数组): <custom_tool_call>name="internet_search", "query"="关键词", "engines"=["google", "wikipedia"], "max_results"=10</custom_tool_call>
                                注意: 
                                1. 参数名（Key）必须加双引号。
                                2. 数组内元素如果是字符串，也必须加双引号。
                                3. 标签必须闭合或以停止符结束。
                            """
                            payload_list.append({"role": "user", "parts": [{"text": error_feedback}]})
                            payload_list.append({"role": "model", "parts": [{"text": CUSTOM_PROMPT_1}]})
                            self.log.warning("Sent parse error feedback to model.")
                        else:
                            # 解析成功：执行工具
                            func_name = custom_tool_data["name"]
                            func_args = custom_tool_data["args"]
                            self.log.info(f"Executing Custom Tool: {func_name}")
                            
                            tool_result = await tool_manager.handle_tool_call(func_name, func_args,**context_kwargs)
                            tool_json_str = json.dumps(tool_result, ensure_ascii=False)
                            
                            tool_return_prompt = f"""
                                [System seed:{current_seed}]:【【【
                                notify : \"工具 {func_name} 执行完毕。\n结果数据: {tool_json_str}\"
                                当前调用轮数:{current_round}/{max_tool_rounds}
                            """
                            if current_round == max_tool_rounds:
                                tool_return_notice = f"""
                                    Notice: \"这是最后一轮调用机会，请立即整合现有信息给出最终结论，严禁再次调用工具，若信息不足请承认失败。\"
                                    】】】
                                """
                            else:
                                tool_return_notice = f"""
                                    Notice: \"请根据以上系统提供的客观事实数据继续执行当前任务或继续下一个工具调用。\"
                                    】】】
                                """
                            tool_return_prompt = tool_return_prompt + tool_return_notice
                            payload_list.append({"role": "user", "parts": [{"text": tool_return_prompt}]})
                            payload_list.append({"role": "model", "parts": [{"text": CUSTOM_PROMPT_1}]})
                        
                        continue

                    # C. 普通文本回复
                    else:
                        usage = parse_gemini_usage(llm_result.get("raw", {}))
                        elapsed = time.time() - start_ts
                        latency_s = round(elapsed, 3)
                        
                        # 获取原始全文进行清洗
                        raw_reply = llm_result.get("text", "")

                        # 1. 去除 Custom Tool Call 标签
                        if GEMINI_TOOL_CALL == "custom":
                             raw_reply = re.sub(r'<custom_tool_call>.*?(?:</custom_tool_call>|$)', '', raw_reply, flags=re.DOTALL)

                        # 2. 去除停止符
                        if "<||reply_end||>" in raw_reply:
                            raw_reply = raw_reply.split("<||reply_end||>")[0]

                        # 3. 处理思维链，只保留思考后的内容
                        display_content = ""
                        if "</think>" in raw_reply:
                            parts = raw_reply.rsplit("</think>", 1)
                            if len(parts) > 1:
                                display_content = parts[1].strip()
                        else:
                            # 如果没有结束标签
                            if current_round < max_tool_rounds:
                                # 如果还没到最后且没输出完思考，强制重试
                                payload_list.append({
                                    "role": "model",
                                    "parts": [{"text": text_content+"\n没有成功输出</think>标签...重新生成回复:<think>"}]
                                })
                                continue 
                            else:
                                display_content = "*(思维卡住了...)*"

                        if not display_content:
                            if "</think>" not in text_content and raw_reply.strip():
                                display_content = raw_reply.strip()
                            else:
                                display_content = "*(Thinking... or Empty Response)*"

                        # 4. 调用新的智能分块
                        final_chunks = smart_split_message(display_content)
                        total_chunks = len(final_chunks)

                        for i, chunk in enumerate(final_chunks):
                            # 构建页脚：Chunk: 0, 1, ...
                            footer_info = [
                                f"Chunk:{i}/{total_chunks - 1}" if total_chunks > 1 else None,
                                f"Time:{latency_s}s",
                                f"In:{usage['input_tokens']}t",
                                f"Out:{usage['output_tokens']}t"
                            ]
                            # 过滤 None，拼接 footer
                            footer_str = " | ".join(filter(None, footer_info))
                            msg_to_send = f"{chunk}\n\n-# {footer_str}"
                            
                            if i == 0:
                                await message.reply(msg_to_send, mention_author=False, suppress_embeds=True)
                            else:
                                await asyncio.sleep(1) # 间隔1秒
                                await message.channel.send(msg_to_send, suppress_embeds=True)

                        return

        except Exception as e:
            self.log.error(f"Chat processing error: {e}", exc_info=True)
            try:
                await message.add_reaction("❌")
            except:
                pass

def parse_gemini_usage(resp: dict) -> dict:
    usage = resp.get("usageMetadata", {})
    return {
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "thought_tokens": usage.get("thoughtsTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
    }

def truncate_base64_data(text: str) -> str:
    BASE64_TRUNCATE_RE = re.compile(
        r'("data"\s*:\s*")([A-Za-z0-9+/=]{20})[A-Za-z0-9+/=]+(")'
)

    return BASE64_TRUNCATE_RE.sub(
        r'\1\2...(truncated)\3',
        text
    )