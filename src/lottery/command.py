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
    DrawTimeType,
    parse_draw_time,
    parse_role_ids,
    validate_lottery_config,
    build_draw_time_preview_text,
    format_lottery_draw_time,
)
from src.lottery import db as lottery_db
from src.lottery.service import generate_quiz_question, has_sufficient_prize_count
from src.lottery.ui import LotteryPreviewView, JoinLotteryView, QuizAnswerView, append_prize_hosting_fields

log = logging.getLogger(__name__)

DEFAULT_ROLE_ID = config.LOTTERY_DEFAULT_ROLE_ID
MAX_PREVIEW_QUEUE = 10


def setup_lottery_commands(tree: app_commands.CommandTree, guild: discord.Object):
    tree.add_command(lottery_group, guild=guild)


lottery_group = app_commands.Group(name="lottery", description="抽奖相关指令")


def build_role_text(required_role_ids: list[int]) -> str:
    return "无" if not required_role_ids else " ".join(f"<@&{role_id}>" for role_id in required_role_ids)


def build_join_requirement_text(min_join_days: int) -> str:
    if min_join_days <= 0:
        return "无"
    return f"加入服务器满 {min_join_days} 天"


@lottery_group.command(name="创建", description="创建一个抽奖活动")
@app_commands.describe(
    title="抽奖标题(可选)",
    participation_mode="参与方式: open/quiz",
    required_roles="允许参与的身份组ID(逗号分隔，可选)",
    min_join_days="加入服务器至少多少天",
    winners_count="中奖人数",
    allow_repeat="是否允许重复中奖",
    draw_after_minutes="多少分钟后开奖(3~10080)",
    draw_at="固定时间点开奖，格式 MM-DD-HH:MM",
    prize_hosting_enabled="是否启用奖品托管",
    random_mode="随机模式: true/pseudo",
)
async def create_lottery(
    interaction: discord.Interaction,
    title: Optional[str] = None,
    participation_mode: str = "open",
    required_roles: Optional[str] = None,
    prize_hosting_enabled: bool = False,
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
        draw_type, draw_time, draw_duration_minutes = parse_draw_time(draw_after_minutes, draw_at)
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
            draw_duration_minutes=draw_duration_minutes,
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
        "prize_hosting_enabled": prize_hosting_enabled,
        "draw_type": config_data.draw_type.value,
        "draw_duration_minutes": config_data.draw_duration_minutes,
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
    embed.add_field(name="加入门槛", value=build_join_requirement_text(config_data.min_join_days), inline=True)
    embed.add_field(name="身份组限制", value=build_role_text(config_data.required_role_ids), inline=False)
    embed.add_field(name="随机模式", value="真随机" if config_data.random_mode == RandomMode.TRUE else "伪随机", inline=True)
    embed.add_field(name="抽奖ID", value=str(lottery_id), inline=False)
    embed.add_field(name="中奖人数", value=str(config_data.winners_count), inline=True)
    embed.add_field(name="重复中奖", value="允许" if config_data.allow_repeat else "不允许", inline=True)
    embed.add_field(
        name="开奖时间",
        value=build_draw_time_preview_text(
            config_data.draw_type,
            config_data.draw_time,
            config_data.draw_duration_minutes,
        ),
        inline=False,
    )
    append_prize_hosting_fields(embed, prize_hosting_enabled, interaction.user.id)
    if question_payload and config_data.participation_mode == ParticipationMode.QUIZ:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
        answer_text = question_payload.get("answer", "")
        if answer_text:
            embed.add_field(name="正确答案", value=answer_text, inline=False)
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
    if lottery.get("status") not in {"preview", "active"}:
        await interaction.followup.send("⚠️ 当前抽奖无法发布参与入口。", ephemeral=True)
        return

    prizes = await lottery_db.list_prizes(bot.db_pool, lottery_id_int)
    if not prizes:
        await interaction.followup.send("⚠️ 请先添加至少一个奖品。", ephemeral=True)
        return

    winners_count = int(lottery.get("winners_count", 1))
    if not has_sufficient_prize_count(len(prizes), winners_count):
        await interaction.followup.send("⚠️ 奖品数量必须大于或等于中奖人数。", ephemeral=True)
        return

    question_payload = lottery.get("question_payload")
    if isinstance(question_payload, str):
        question_payload = json.loads(question_payload)

    draw_type = lottery.get("draw_type")
    original_draw_time = lottery.get("draw_time")
    actual_draw_time = original_draw_time
    activated_now = False

    if lottery.get("status") == "preview":
        now = datetime.datetime.now(config.UTC_PLUS_8)
        if draw_type == DrawTimeType.DURATION.value:
            draw_duration_minutes = lottery.get("draw_duration_minutes")
            if draw_duration_minutes is None:
                await interaction.followup.send("❌ 相对开奖时长配置缺失，请重新编辑抽奖配置后再试。", ephemeral=True)
                return
            actual_draw_time = now + datetime.timedelta(minutes=int(draw_duration_minutes))
        else:
            if actual_draw_time <= now:
                await interaction.followup.send("❌ 固定开奖时间已早于当前时间，请先修改抽奖配置。", ephemeral=True)
                return
        try:
            await lottery_db.update_lottery_draw_time(bot.db_pool, lottery_id_int, actual_draw_time)
            await lottery_db.update_lottery_status(bot.db_pool, lottery_id_int, "active")
            activated_now = True
        except Exception:
            log.exception("发布抽奖 %s 前更新状态或开奖时间失败。", lottery_id_int)
            await interaction.followup.send("❌ 发布失败，请稍后再试。", ephemeral=True)
            return

    role_ids = json.loads(lottery.get("required_role_ids", "[]"))
    role_text = build_role_text(role_ids)
    embed = discord.Embed(
        title=lottery.get("title") or "抽奖活动",
        description="点击参与按钮或回答问题参与抽奖。",
        color=discord.Color.green(),
    )
    embed.add_field(name="开奖时间", value=format_lottery_draw_time(actual_draw_time), inline=False)
    embed.add_field(name="加入门槛", value=build_join_requirement_text(int(lottery.get("min_join_days", 0))), inline=True)
    embed.add_field(name="身份组限制", value=role_text, inline=False)
    embed.add_field(name="中奖人数", value=str(winners_count), inline=True)
    embed.add_field(name="重复中奖", value="允许" if lottery.get("allow_repeat") else "不允许", inline=True)

    if lottery.get("participation_mode") == ParticipationMode.QUIZ.value and question_payload:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
        view = QuizAnswerView(lottery_id_int, question_payload.get("question", ""), question_payload.get("options", []))
    else:
        view = JoinLotteryView(lottery_id_int)

    try:
        await interaction.channel.send(embed=embed, view=view)
    except Exception:
        if activated_now:
            try:
                await lottery_db.update_lottery_draw_time(bot.db_pool, lottery_id_int, original_draw_time)
                await lottery_db.update_lottery_status(bot.db_pool, lottery_id_int, "preview")
            except Exception:
                log.exception("发布抽奖 %s 失败后回滚状态失败。", lottery_id_int)
        log.exception("发布抽奖 %s 的参与入口失败。", lottery_id_int)
        await interaction.followup.send("❌ 发布抽奖参与入口失败，请稍后再试。", ephemeral=True)
        return

    if activated_now:
        try:
            await lottery_db.delete_preview_queue(bot.db_pool, interaction.user.id)
        except Exception:
            log.exception("发布抽奖 %s 后删除预览队列失败。", lottery_id_int)

    await interaction.followup.send(
        f"✅ 已发布抽奖参与入口。当前开奖时间：{format_lottery_draw_time(actual_draw_time)}",
        ephemeral=True,
    )
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
    embed = discord.Embed(
        title=lottery.get("title") or "抽奖预览",
        description="当前抽奖预览详情。",
        color=discord.Color.blue(),
    )
    embed.add_field(name="参与方式", value=lottery.get("participation_mode"), inline=True)
    embed.add_field(name="加入门槛", value=build_join_requirement_text(int(lottery.get("min_join_days", 0))), inline=True)
    embed.add_field(name="身份组限制", value=build_role_text(role_ids), inline=False)
    embed.add_field(name="抽奖ID", value=str(lottery.get("lottery_id")), inline=False)
    embed.add_field(name="中奖人数", value=str(lottery.get("winners_count")), inline=True)
    embed.add_field(name="重复中奖", value="允许" if lottery.get("allow_repeat") else "不允许", inline=True)
    embed.add_field(
        name="开奖时间",
        value=build_draw_time_preview_text(
            lottery.get("draw_type", DrawTimeType.FIXED.value),
            lottery.get("draw_time"),
            lottery.get("draw_duration_minutes"),
        ),
        inline=False,
    )
    append_prize_hosting_fields(embed, bool(lottery.get("prize_hosting_enabled")), int(lottery.get("creator_id", 0)))
    question_payload = lottery.get("question_payload")
    if isinstance(question_payload, str):
        question_payload = json.loads(question_payload)
    if question_payload and lottery.get("participation_mode") == ParticipationMode.QUIZ.value:
        embed.add_field(name="答题门槛", value=question_payload.get("question", ""), inline=False)
        answer_text = question_payload.get("answer", "")
        if answer_text:
            embed.add_field(name="正确答案", value=answer_text, inline=False)
    await interaction.followup.send(embed=embed, view=LotteryPreviewView(lottery.get("lottery_id")), ephemeral=True)
