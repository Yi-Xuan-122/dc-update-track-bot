import discord
from discord.ext import commands
import logging
import json
from src.chat.chat_env import system_prompt , SYSTEM_PROMPT
from src.config import ADMIN_IDS
from src.chat.gemini_format import gemini_format_callback
from src.config import LLM_FORMAT , LLM_ALLOW_CHANNELS ,ADMIN_IDS
from src.chat.chat_aux import parse_message_history_to_prompt
from src.llm import LLM
from src.summary.summary_aux import RateLimitingScheduler
import time
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
        # 1. 基础过滤：不回复自己，不回复其他机器人
        if message.author.bot:
            return

        # 2. 频道鉴权
        if not self.is_channel_allowed(message.channel):
            return

        # 3. 触发检测
        if not self.is_triggered(message):
            return

        # --- 开始处理 ---
        try:
            # 显示 "正在输入..." 状态，提升用户体验
            async with message.channel.typing():
                
                # 4. 获取上下文
                # 获取最近的 30 条消息作为上下文 (包含当前这条触发消息)
                # history 返回的是倒序的 (最新的在前)，我们需要把它正序排列
                history_messages = [msg async for msg in message.channel.history(limit=30)]
                history_messages.reverse() 

                # 5. 构建 Prompt (复用 summary 的核心函数)
                # 注意：这里不需要传入 members 列表，因为聊天模式下我们需要看到所有人的发言
                
                if LLM_FORMAT == "gemini":
                    final_data = await parse_message_history_to_prompt(
                        message=history_messages,
                        post_processing_callback=gemini_format_callback,
                        bot_user=self.bot.user,
                        admin_ids=ADMIN_IDS
                    )
                    payload_list = final_data.get("contents", [])

                    gemini_system = {
                        "systemInstruction": {
                            "parts": [{"text": SYSTEM_PROMPT}]
                        }
                    }
                    # 这里为了兼容性，我们将 system prompt 伪装成第一条消息，稍后在 llm.py 中提取
                    payload_list.insert(0, gemini_system)
                    
                else:
                    # OpenAI 格式
                    final_prompt = await parse_message_history_to_prompt(
                        message=history_messages,
                        post_processing_callback=gemini_format_callback,
                        bot_user=self.bot.user,
                        admin_ids=ADMIN_IDS
                    )
                    payload_list = final_prompt.get("messages", [])
                    system_payload = {"role": "system", "content": SYSTEM_PROMPT}
                    payload_list.insert(0, system_payload)

                # 6. 调用 LLM
                llm_input_json = json.dumps(payload_list, ensure_ascii=False)
                self.log.debug(f"Chat Playload:\n{llm_input_json}")
                start_ts = time.time()
                llm_result = await self.llm.llm_call(llm_input_json)
                response_chunks = llm_result["chunks"]
                usage = parse_gemini_usage(llm_result["raw"])
                input_tokens = usage["input_tokens"]
                output_tokens = usage["output_tokens"]
                elapsed = time.time() - start_ts
                latency_s = round(elapsed, 3)

                if not response_chunks:
                    await message.reply("*(...似乎陷入了沉思，没有回应...)*")
                    return

                # 7. 发送回复
                # 通常聊天回复比较短，取第一个 chunk 即可。
                # 如果很长，可以分段发送
                reply_content = response_chunks[0] + f"\n\n-# Time:{latency_s}s | In :{input_tokens}t | Out :{output_tokens}t"
                
                if "System Seed:" in reply_content:
                    reply_content = reply_content.split("System Seed:")[0]

                # 回复用户
                await message.reply(reply_content, mention_author=False)

        except Exception as e:
            self.log.error(f"Chat processing error: {e}", exc_info=True)
            # 聊天模式下出错通常不发报错信息给用户，以免打断沉浸感，或者发个简单的表情
            await message.add_reaction("😵")

def parse_gemini_usage(resp: dict) -> dict:
    usage = resp.get("usageMetadata", {})

    return {
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "thought_tokens": usage.get("thoughtsTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
    }