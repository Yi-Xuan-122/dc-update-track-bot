import discord
from discord import app_commands
from discord.ext import commands 
import json
import logging
from src.llm import LLM
from src.summary.summary_aux import parse_user_ids , RateLimitingScheduler , Summary_fetch_task
from src.summary.summary_ui import summary_check_view
from typing import List, Optional
from src.config import LLM_FORMAT , LLM_ALLOW_CHANNELS
import asyncio
import time
import re

log = logging.getLogger(__name__)


class summarizerCog(commands.Cog):
    def __init__(self,bot: commands.Bot):
        self.bot = bot
        self.scheduler = RateLimitingScheduler(bot)
        self.scheduler_task : Optional[asyncio.Task] = None
        self.cache_task: Optional[asyncio.Task] = None
        try:
            self.llm = LLM()
            logging.info("Summary Cog loaded")
        except Exception as e:
            self.llm = None
            logging.error(f"Failed to load Summary Cog: {e}")

    def cog_load(self):
            logging.debug("Scheduler task is starting.")
            self.scheduler_task = self.bot.loop.create_task(self.scheduler.run())
            self.cache_task = self.bot.loop.create_task(self.scheduler.queue_cache_event())
    
    def cog_unload(self):
        logging.debug("Scheduler task is being cancelled.")
        if self.scheduler_task:
            self.scheduler_task.cancel()
        if self.cache_task:
            self.cache_task.cancel()

    summary_group = app_commands.Group(name="summary",description="文本摘要相关命令")
    @summary_group.command(name="常规",description="从URL自下往上获取特定条数的消息进行总结")
    @app_commands.describe(
        url = "最终的消息链接",
        message_length = "将要获取的上下文条数",
        members = "(可选)若填写此项，则只有这些成员的消息会被总结，直接at即可"
    )
    async def summary(self, interaction: discord.Integration , url: str , message_length: int,members: str=None):
        current_channel_id = interaction.channel_id
        # 获取父频道 ID (如果当前是 Thread/子区，则 parent_id 不为空)
        parent_id = getattr(interaction.channel, "parent_id", None)

        # 检查当前频道或其父频道是否在允许列表中
        if current_channel_id not in LLM_ALLOW_CHANNELS and parent_id not in LLM_ALLOW_CHANNELS:
            await interaction.response.send_message(
                f"❌ 权限不足：当前频道 (ID: {current_channel_id}) 未被授权使用 AI 总结功能。",
                ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        user_ids = parse_user_ids(members)
        match = re.match(r'https://discord.com/channels/(\d+)/(\d+)/(\d+)', url)
        if not match:
            await interaction.followup.send(
                f"❌ 提供的URL格式无效。请提供一条完整的消息链接。",
                ephemeral=True
            )
            return
        guild_id,channel_id,message_id = match.groups()
        current_task = Summary_fetch_task(
            timestamp=time.time(),
            target_channel=int(channel_id),
            fetch_total=message_length,
            single_limit=min(message_length,100),
            start_before_message_id=message_id
        )
        try:
            estimated_time_s = await self.scheduler.estimate_completion_time(current_task)
            estimated_time_str = f"{estimated_time_s:.2f} 秒"
            if estimated_time_s > 60:
                estimated_time_str = f"{estimated_time_s} 秒 (约{float(estimated_time_s / 60):.2f} 分钟)"
        except Exception as e:
            estimated_time_s = "无法估计时间 (Error:{e})"
        if members == None:
            members = "上下文中的**全部成员**"
        embed_title = "⭕|Summary二次确认"
        embed_text = f"""
        🔗|[起始点]({url})
        🔼|消息条数:{message_length}
        🙍‍♂️|命令指定的成员: {members}
        ⏳|预计时间: {estimated_time_str}
        """

        embed = discord.Embed(title=embed_title,description=embed_text,color=discord.Color.blue())
        check_view = summary_check_view()

        await interaction.edit_original_response(embed=embed,view=check_view)
        await check_view.wait()

        if check_view.value is not True:
            if check_view.value is None:
                await interaction.edit_original_response(content="⏲|操作超时，已取消",view=None)
                return
            await interaction.edit_original_response(content=f"已取消，您可以修改您的命令再次发送\n```\n/summary 常规 url:{url} message_length:{message_length} members:{members}\n```",view=None)
            return
        try:
            await self.scheduler.main_queue.put(current_task)
            self.scheduler.rate_limit_event.set()

            message: List[discord.Message] = await asyncio.wait_for(current_task.future,timeout=600)

            if not message:
                await interaction.edit_original_response(content="🟡 | 未能获取到任何消息，请检查频道权限或链接是否正确。", view=None)
                return
            
            await interaction.edit_original_response(embed=discord.Embed(
                title="⚙️|正在处理",
                description=f"已成功获取 {len(message)} 条消息，正在进行 AI 总结...",
                color=discord.Color.green()
            ),view=None)

            from src.summary.summary_aux import parse_message_history_to_prompt, openai_format
            from src.chat.gemini_format import gemini_format_callback
            
            if LLM_FORMAT == "gemini":
                # 获取 contents 结构
                final_data = await parse_message_history_to_prompt(
                    message=message,
                    post_processing_callback=gemini_format_callback,
                    members_ids=user_ids if user_ids else None
                )
                payload_list = final_data.get("contents", [])
                
                # 转换 System Prompt 为 Gemini 格式
                from src.summary.summary_env import SYSTEM_PROMPT
                
                gemini_system = {
                    "systemInstruction": {
                        "parts": [{"text": SYSTEM_PROMPT}]
                    }
                }
                payload_list.insert(0, gemini_system)
            else:
                # 原有的 OpenAI 处理逻辑
                final_prompt = await parse_message_history_to_prompt(
                    message=message,
                    post_processing_callback=openai_format,
                    members_ids=user_ids if user_ids else None
                )
                payload_list = final_prompt.get("messages", [])
                from src.summary.summary_env import system_prompt
                payload_list.insert(0, system_prompt)

            llm_input_json = json.dumps(payload_list, ensure_ascii=False)
            logging.debug(f"llm_input_json:\n {llm_input_json}")
            try:
                llm_result = await self.llm.llm_call(llm_input_json)
                summary_chunks = llm_result["chunks"]
                if not summary_chunks:
                    await interaction.edit_original_response(content="❌ | AI 未能生成任何总结内容。")
                    return
                 # 1. 首先更新之前的私密信息，告知用户已完成
                await interaction.edit_original_response(
                    content="✅ | 总结已完成，结果已发送至频道中。", 
                    embed=None, 
                    view=None
                )

                # 2. 构造主 Embed
                main_embed = discord.Embed(
                    title="📝 | 聊天摘要报告",
                    description=summary_chunks[0],
                    color=discord.Color.gold()
                )
                main_embed.set_footer(text=f"分析了 {len(message)} 条消息 | 模式: {LLM_FORMAT}")
                
                # 3. 使用 followup.send 并且设置 ephemeral=False (默认就是 False，但为了明确可以加上)
                # 这条消息频道内所有人可见
                await interaction.followup.send(content=f"{interaction.user.mention} 提交的总结报告：", embed=main_embed, ephemeral=False)

                # 4. 如果有后续分段，同样公开发送
                for i in range(1, len(summary_chunks)):
                    next_embed = discord.Embed(
                        description=summary_chunks[i],
                        color=discord.Color.gold()
                    )
                    await interaction.followup.send(embed=next_embed, ephemeral=False)

            except Exception as e:
                # 发生错误时，依然在私密消息中反馈
                log.error(f"LLM 调用失败: {e}", exc_info=True)
                await interaction.edit_original_response(content=f"❌ | 调用 AI 时出错: {str(e)}", view=None)

        except asyncio.TimeoutError:
            await interaction.edit_original_response(content="❌ | 处理超时（消息抓取或 AI 响应过慢）。")
        except Exception as e:
            log.error(f"发生异常: {e}", exc_info=True)
            await interaction.edit_original_response(content=f"❌ | 程序运行出错: {str(e)}")