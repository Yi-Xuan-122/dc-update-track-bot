import discord
from discord.ext import commands
import logging
import json
from src.chat.chat_env import system_prompt , SYSTEM_PROMPT,CUSTOM_PROMPT_1
from src.config import ADMIN_IDS
from src.chat.gemini_format import gemini_format_callback
from src.config import LLM_FORMAT , LLM_ALLOW_CHANNELS ,ADMIN_IDS
from src.chat.chat_aux import parse_message_history_to_prompt
from src.llm import LLM
from src.summary.summary_aux import RateLimitingScheduler
import time
from src.chat.tool_src import tool_manager
class LLM_Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.llm = LLM()
        self.scheduler = RateLimitingScheduler(bot)
        self.log = logging.getLogger("LLM_Chat")
    
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
        """检查频道权限（支持子区）"""
        if channel.id in LLM_ALLOW_CHANNELS:
            return True
        if hasattr(channel, "parent_id") and channel.parent_id in LLM_ALLOW_CHANNELS:
            return True
        return False

    def is_triggered(self, message: discord.Message) -> bool:
        """
        判断是否触发回复逻辑：
        1. 消息中提及了 Bot (At)
        2. 消息回复了 Bot 的消息
        """
        # 情况1: 直接 At
        if self.bot.user in message.mentions:
            return True
        
        # 情况2: 回复链检测
        if message.reference and message.reference.cached_message:
            # 如果能获取到缓存的消息对象，直接判断作者
            if message.reference.cached_message.author.id == self.bot.user.id:
                return True
        elif message.reference:
            # 如果没有缓存（比如消息太久远），尝试通过 resolve (API调用可能较慢，通常 message.reference.resolved 更好)
            # 简单起见，这里主要依赖 message.mentions，回复通常伴随 mention
            # 如果需要强回复检测（即使回复时关掉了 mention），需要 fetch_message，但会增加 API 消耗
            pass
            
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ---------- 基础过滤 ----------
        if message.author.bot:
            return

        if not self.is_channel_allowed(message.channel):
            return

        if not self.is_triggered(message):
            return

        max_tool_rounds = 8
        current_round = 0

        try:
            async with message.channel.typing():

                # ---------- 1. 拉取上下文 ----------
                history_messages = [
                    msg async for msg in message.channel.history(limit=30)
                ]
                history_messages.reverse()

                # ---------- 2. 构建 Prompt ----------
                if LLM_FORMAT == "gemini":
                    final_data = await parse_message_history_to_prompt(
                        message=history_messages,
                        post_processing_callback=gemini_format_callback,
                        bot_user=self.bot.user,
                        admin_ids=ADMIN_IDS
                    )

                    payload_list = final_data.get("contents", [])

                    # Gemini systemInstruction（伪装，后续在 llm_call 拆）
                    payload_list.insert(0, {
                        "systemInstruction": {
                            "parts": [{"text": SYSTEM_PROMPT}]
                        }
                    })
                    # 插入最后一条的消息
                    payload_list.insert(1,{
                        "role": 'model',
                        "parts": [ { "text": CUSTOM_PROMPT_1 } ]
                    })

                else:
                    final_data = await parse_message_history_to_prompt(
                        message=history_messages,
                        post_processing_callback=gemini_format_callback,
                        bot_user=self.bot.user,
                        admin_ids=ADMIN_IDS
                    )

                    payload_list = final_data.get("messages", [])
                    payload_list.insert(0, {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    })

                # ---------- 3. Tool / LLM 循环 ----------
                start_ts = time.time()

                while current_round < max_tool_rounds:
                    current_round += 1

                    llm_input_json = json.dumps(payload_list, ensure_ascii=False)
                    self.log.debug(f"LLM Payload:\n{llm_input_json}")

                    llm_result = await self.llm.llm_call(
                        llm_input_json,
                        include_tools=True
                    )
                    self.log.debug("===== RAW LLM RESULT =====")
                    self.log.debug(llm_result)
                    self.log.debug("===== END RAW LLM RESULT =====")

                    # ---------- 4. 普通文本回复 ----------
                    result_type = llm_result.get("type")
                    if result_type == "text":
                        response_chunks = llm_result["chunks"]
                        usage = parse_gemini_usage(llm_result.get("raw", {}))

                        elapsed = time.time() - start_ts
                        latency_s = round(elapsed, 3)

                        reply_content = response_chunks[0]

                        if "System Seed:" in reply_content:
                            reply_content = reply_content.split("System Seed:")[0]

                        reply_content += (
                            f"\n\n-# Time:{latency_s}s | "
                            f"In:{usage['input_tokens']}t | "
                            f"Out:{usage['output_tokens']}t"
                        )

                        await message.reply(reply_content, mention_author=False)
                        return

                    # ---------- 5. Tool 调用 ----------
                    if result_type == "tool_call":
                        func_name = llm_result["name"]
                        func_args = llm_result["args"]
                        original_parts = llm_result.get("parts_trace")

                        self.log.info(f"Tool Call → {func_name}({func_args})")

                        tool_result = await tool_manager.handle_tool_call(
                            func_name,
                            func_args
                        )

                        if original_parts:
                            payload_list.append({
                                "role": "model",
                                "parts": original_parts 
                            })
                        else:
                            # 兜底
                            payload_list.append({
                                "role": "model",
                                "parts": [{
                                    "functionCall": {
                                        "name": func_name,
                                        "args": func_args
                                    }
                                }]
                            })
                        if tool_result.get("results"):
                            payload_list.append({
                                "role": "function",
                                "parts": [{
                                    "functionResponse": {
                                        "name": func_name,
                                        "response": {
                                            "content": tool_result
                                        }
                                    }
                                }]
                            })
                        else:
                            payload_list.append({
                                "role": "function",
                                "parts": [{
                                    "functionResponse": {
                                        "name": func_name,
                                        "response": {
                                            "content": {
                                                "query": func_args.get("query"),
                                                "results": [],
                                                "notice": "⚠️ 没有找到匹配结果"
                                            }
                                        }
                                    }
                                }]
                            })

                        continue

                # ---------- 6. 兜底 ----------
                await message.reply(
                    "*(思考好像绕进死胡同了…要不换个问法？)*",
                    mention_author=False
                )

        except Exception as e:
            self.log.error(f"Chat processing error: {e}", exc_info=True)
            await message.add_reaction("😵")


def parse_gemini_usage(resp: dict) -> dict:
    usage = resp.get("usageMetadata", {})

    return {
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "thought_tokens": usage.get("thoughtsTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
    }

async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    webhooks = await channel.webhooks()
    for wh in webhooks:
        if wh.name == "LLM-Chat":
            return wh

    return await channel.create_webhook(name="LLM-Chat")