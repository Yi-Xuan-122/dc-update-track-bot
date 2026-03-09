from __future__ import annotations
import datetime
import json
import logging
from typing import Optional
import discord
from discord import app_commands
from src import config
from src.lottery.models import (
    LotteryConfig,
    LotteryConfigError,
    ParticipationMode,
    RandomMode,
    parse_draw_time,
    parse_role_ids,
    validate_lottery_config,
    LotteryStatus,
)
from src.lottery import db as lottery_db
from src.lottery.service import generate_quiz_question, ensure_transition
from src.lottery.ui import LotteryPreviewView, JoinLotteryView, QuizAnswerView

log = logging.getLogger(__name__)

DEFAULT_ROLE_ID = config.LOTTERY_DEFAULT_ROLE_ID
MAX_PREVIEW_QUEUE = 10


def setup_lottery_commands(tree: app_commands.CommandTree, guild: discord.Object):
    tree.add_command(lottery_group, guild=guild)


lottery_group = app_commands.Group(name="lottery", description="抽奖相关指令")


@lottery_group.command(name="创建", description="创建一个抽奖活动")
@app_commands.describe(
    title="抽奖标题(可选)",
    participation_mode="参与方式: open/quiz",
    required_roles="允许参与的身份组ID(逗号分隔，可选)",
    min_join_days="加入服务器至少多少天",
    winners_count="中奖人数",
    allow_repeat="是否允许重复中奖",
    draw_after_minutes="多少分钟后开奖(30~10080)",
    draw_at="固定时间点开奖，格式 MM-DD-HH:MM",
    random_mode="随机模式: true/pseudo",
)
async def create_lottery(
    interaction: discord.Interaction,
    title: Optional[str] = None,
    participation_mode: str = "open",
    required_roles: Optional[str] = None,
    min_join_days: int = 0,
    winners_count: int = 1,
    allow_repeat: bool = False,
    draw_after_minutes: Optional[int] = None,
    draw_at: Optional[str] = None,
    random_mode: str = "true",
):
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client

    try:
        queue_count = await lottery_db.get_preview_queue_count(bot.db_pool)
        if queue_count >= MAX_PREVIEW_QUEUE:
            await interaction.followup.send("⚠️ 当前创建队列已满，请稍后再试。", ephemeral=True)
            return
        existing_preview = await lottery_db.get_preview_by_creator(bot.db_pool, interaction.user.id)
        if existing_preview:
            await interaction.followup.send("⚠️ 你已有一个抽奖预览在配置中，请先完成或等待过期。", ephemeral=True)
            return

        role_ids = parse_role_ids(required_roles, DEFAULT_ROLE_ID)
        draw_type, draw_time = parse_draw_time(draw_after_minutes, draw_at)
        mode = ParticipationMode.QUIZ if participation_mode.lower() == "quiz" else ParticipationMode.OPEN

        mode_value = RandomMode.TRUE if random_mode.lower() == "true" else RandomMode.PSEUDO
        config_data = LotteryConfig(
            title=title,
            participation_mode=mode,
            required_role_ids=role_ids,
            min_join_days=min_join_days,
            winners_count=winners_count,
            allow_repeat=allow_repeat,
            random_mode=mode_value,
            draw_type=draw_type,
            draw_time=draw_time,
        )
        validate_lottery_config(config_data)
    except LotteryConfigError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return

    lottery_id = interaction.id
    preview_expires_at = datetime.datetime.now(config.UTC_PLUS_8) + datetime.timedelta(hours=1)
    question_payload = None
    if config_data.participation_mode == ParticipationMode.QUIZ:
        try:
            question_payload = await generate_quiz_question()
        except LotteryConfigError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

    payload = {
        "lottery_id": lottery_id,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "creator_id": interaction.user.id,
        "title": config_data.title,
        "status": "preview",
        "participation_mode": config_data.participation_mode.value,
        "required_role_ids": config_data.required_role_ids,
        "min_join_days": config_data.min_join_days,
        "winners_count": config_data.winners_count,
        "allow_repeat": config_data.allow_repeat,
        "random_mode": config_data.random_mode.value,
        "draw_type": config_data.draw_type.value,
        "draw_time": config_data.draw_time,
        "preview_expires_at": preview_expires_at,
        "question_payload": question_payload,
    }

    try:
        existing_preview = await lottery_db.get_preview_by_creator(bot.db_pool, interaction.user.id)
        if existing_preview:
            await interaction.followup.send("⚠️ 你已有正在配置的抽奖，请先修改或关闭该预览。", ephemeral=True)
            return
        await lottery_db.insert_lottery(bot.db_pool, payload)
        await lottery_db.insert_preview_queue(bot.db_pool, interaction.user.id, lottery_id)
        if getattr(bot, "lottery_scheduler", None):
            bot.lottery_scheduler.wake_up()
    except Exception as exc:
        log.error("创建抽奖失败: %s", exc)
        await interaction.followup.send("❌ 创建抽奖失败，请稍后再试。", ephemeral=True)
        return

    embed = discord.Embed(
        title=config_data.title or "抽奖预览",
        description="抽奖预览有效期 1 小时，请确认配置。",
        color=discord.Color.blue(),
    )
    embed.add_field(name="参与方式", value=config_data.participation_mode.value, inline=True)
    embed.add_field(name="加入门槛", value=f"加入服务器满 {config_data.min_join_days} 天", inline=True)
    role_text = "无" if not config_data.required_role_ids else " ".join(f"<@&{r}>" for r in config_data.required_role_ids)
    embed.add_field(name="身份组限制", value=role_text, inline=False)
    embed.add_field(name="随机模式", value="真随机" if config_data.random_mode == RandomMode.TRUE else "伪随机", inline=True)
    embed.add_field(name="抽奖ID", value=str(lottery_id), inline=False)
    embed.add_field(name="中奖人数", value=str(config_data.winners_count), inline=True)
    embed.add_field(name="重复中奖", value="允许" if config_data.allow_repeat else "不允许", inline=True)
    embed.add_field(name="开奖时间", value=config_data.draw_time.strftime("%Y-%m-%d %H:%M UTC+8"), inline=False)
    if question_payload:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
    await interaction.followup.send(embed=embed, view=LotteryPreviewView(lottery_id), ephemeral=True)


@lottery_group.command(name="开始", description="发布抽奖参与入口")
@app_commands.describe(lottery_id="抽奖ID(创建时的交互ID)")
async def publish_lottery(interaction: discord.Interaction, lottery_id: str):
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    try:
        lottery_id_int = int(lottery_id)
    except ValueError:
        await interaction.followup.send("❌ 抽奖ID无效。", ephemeral=True)
        return
    lottery = await lottery_db.get_lottery(bot.db_pool, lottery_id_int)
    if not lottery:
        await interaction.followup.send("❌ 抽奖不存在。", ephemeral=True)
        return
    if lottery.get("creator_id") != interaction.user.id:
        await interaction.followup.send("❌ 只有创建者可以发布抽奖。", ephemeral=True)
        return
    if lottery.get("status") != "active":
        await interaction.followup.send("⚠️ 抽奖未处于进行中状态。", ephemeral=True)
        return
    mode = lottery.get("participation_mode")
    question_payload = lottery.get("question_payload")
    if isinstance(question_payload, str):
        question_payload = json.loads(question_payload)
    embed = discord.Embed(
        title=lottery.get("title") or "抽奖活动",
        description="点击参与按钮或回答问题参与抽奖。",
        color=discord.Color.green(),
    )
    view = None
    if mode == ParticipationMode.QUIZ.value and question_payload:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
        view = QuizAnswerView(lottery_id_int, question_payload.get("question", ""), question_payload.get("options", []))
    else:
        view = JoinLotteryView(lottery_id_int)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.followup.send("✅ 已发布抽奖参与入口。", ephemeral=True)
    if getattr(bot, "lottery_scheduler", None):
        bot.lottery_scheduler.wake_up()


@lottery_group.command(name="管理", description="查看并管理当前抽奖预览")
async def manage_lottery(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    preview = await lottery_db.get_preview_by_creator(bot.db_pool, interaction.user.id)
    if not preview:
        await interaction.followup.send("⚠️ 你当前没有在配置中的抽奖。", ephemeral=True)
        return
    lottery = await lottery_db.get_lottery(bot.db_pool, preview["lottery_id"])
    if not lottery:
        await interaction.followup.send("❌ 抽奖不存在或已失效。", ephemeral=True)
        return
    role_ids = json.loads(lottery.get("required_role_ids", "[]"))
    role_text = "无" if not role_ids else " ".join(f"<@&{r}>" for r in role_ids)
    embed = discord.Embed(
        title=lottery.get("title") or "抽奖预览",
        description="当前抽奖预览详情。",
        color=discord.Color.blue(),
    )
    embed.add_field(name="参与方式", value=lottery.get("participation_mode"), inline=True)
    embed.add_field(name="加入门槛", value=f"加入服务器满 {lottery.get('min_join_days')} 天", inline=True)
    embed.add_field(name="身份组限制", value=role_text, inline=False)
    embed.add_field(name="抽奖ID", value=str(lottery.get("lottery_id")), inline=False)
    embed.add_field(name="中奖人数", value=str(lottery.get("winners_count")), inline=True)
    embed.add_field(name="重复中奖", value="允许" if lottery.get("allow_repeat") else "不允许", inline=True)
    embed.add_field(
        name="开奖时间",
        value=lottery.get("draw_time").strftime("%Y-%m-%d %H:%M UTC+8"),
        inline=False,
    )
    question_payload = lottery.get("question_payload")
    if isinstance(question_payload, str):
        question_payload = json.loads(question_payload)
    if question_payload:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
    await interaction.followup.send(embed=embed, view=LotteryPreviewView(lottery.get("lottery_id")), ephemeral=True)
