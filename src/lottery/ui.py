from __future__ import annotations
import asyncio
import datetime
import logging
import json
from collections import Counter
from typing import List, Optional
import discord
from discord import ui
from src.lottery.models import (
    validate_prize_text,
    LotteryConfig,
    LotteryConfigError,
    parse_draw_time,
    parse_role_ids,
    validate_lottery_config,
    ParticipationMode,
    RandomMode,
)
from src.lottery import db as lottery_db
from src.lottery.service import shuffle_options, check_member_eligibility

log = logging.getLogger(__name__)

class PrizeModal(ui.Modal):
    def __init__(self, lottery_id: int, title: str = "新增奖品"):
        super().__init__(title=title, timeout=300)
        self.lottery_id = lottery_id
        self.prize_input = ui.TextInput(
            label="奖品内容（<=200字符）",
            style=discord.TextStyle.paragraph,
            max_length=200,
            required=True,
        )
        self.add_item(self.prize_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bot = interaction.client
            raw_text = self.prize_input.value
            lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
            if not lines:
                raise LotteryConfigError("奖品内容不能为空。")
            existing_count = await lottery_db.count_prizes(bot.db_pool, self.lottery_id)
            total_added = 0
            for line in lines:
                if ":::" in line:
                    name_part, count_part = line.split(":::", 1)
                    name = validate_prize_text(name_part.strip())
                    if not count_part.strip().isdigit():
                        raise LotteryConfigError("奖品数量必须是正整数。")
                    count = int(count_part.strip())
                    if count <= 0:
                        raise LotteryConfigError("奖品数量必须大于 0。")
                    if count > 100:
                        raise LotteryConfigError("单条奖品数量不能超过 100。")
                    for _ in range(count):
                        if existing_count + total_added + 1 > 100:
                            raise LotteryConfigError("单次抽奖奖品总数不能超过 100。")
                        await lottery_db.insert_prize(bot.db_pool, self.lottery_id, name)
                        total_added += 1
                else:
                    prize_text = validate_prize_text(line)
                    if existing_count + total_added + 1 > 100:
                        raise LotteryConfigError("单次抽奖奖品总数不能超过 100。")
                    await lottery_db.insert_prize(bot.db_pool, self.lottery_id, prize_text)
                    total_added += 1
            await interaction.response.send_message(f"✅ 已添加 {total_added} 个奖品。", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"❌ 添加失败: {exc}", ephemeral=True)


def build_prize_summary(prizes: List[dict]) -> str:
    if not prizes:
        return "(暂无奖品)"
    counter = Counter(p["prize_text"] for p in prizes)
    lines = [f"{name} : {count}份" for name, count in sorted(counter.items(), key=lambda x: x[0])]
    return "\n".join(lines)


def build_prize_manage_embed(prizes: List[dict]) -> discord.Embed:
    total = len(prizes)
    embed = discord.Embed(title="奖品管理", color=discord.Color.blue())
    embed.add_field(name="总数", value=str(total), inline=False)
    embed.add_field(name="明细", value=build_prize_summary(prizes), inline=False)
    return embed


class PrizeBulkDeleteModal(ui.Modal):
    def __init__(self, lottery_id: int, prizes: List[dict]):
        super().__init__(title="批量删除奖品", timeout=300)
        self.lottery_id = lottery_id
        self.prizes = prizes
        self.prize_input = ui.TextInput(
            label="奖品名(可用 :::数量)，每行一个",
            style=discord.TextStyle.paragraph,
            required=True,
        )
        self.add_item(self.prize_input)

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        try:
            lines = [line.strip() for line in self.prize_input.value.splitlines() if line.strip()]
            if not lines:
                raise LotteryConfigError("请输入要删除的奖品名称。")
            prize_map = {}
            for prize in self.prizes:
                prize_map.setdefault(prize["prize_text"], []).append(prize["prize_id"])
            delete_ids: List[int] = []
            for line in lines:
                if ":::" in line:
                    name_part, count_part = line.split(":::", 1)
                    name = validate_prize_text(name_part.strip())
                    if not count_part.strip().isdigit():
                        raise LotteryConfigError("删除数量必须为正整数。")
                    count = int(count_part.strip())
                    if count <= 0:
                        raise LotteryConfigError("删除数量必须大于 0。")
                    ids = prize_map.get(name, [])
                    delete_ids.extend(ids[:count])
                else:
                    name = validate_prize_text(line)
                    delete_ids.extend(prize_map.get(name, []))
            delete_ids = list(dict.fromkeys(delete_ids))
            if not delete_ids:
                await interaction.response.send_message("⚠️ 未找到可删除的奖品。", ephemeral=True)
                return
            await lottery_db.delete_prizes_by_ids(bot.db_pool, self.lottery_id, delete_ids)
            await interaction.response.send_message(f"✅ 已删除 {len(delete_ids)} 个奖品。", ephemeral=True)
        except LotteryConfigError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"❌ 删除失败: {exc}", ephemeral=True)


class LotteryEditModal(ui.Modal):
    def __init__(self, lottery_id: int, lottery: dict):
        super().__init__(title="修改抽奖配置", timeout=300)
        self.lottery_id = lottery_id
        self.original_title = lottery.get("title") or None
        self.roles_input = ui.TextInput(
            label="身份组ID(逗号分隔)",
            required=False,
            default=",".join(str(r) for r in json.loads(lottery.get("required_role_ids", "[]"))),
        )
        self.join_days_input = ui.TextInput(
            label="加入服务器至少多少天",
            required=True,
            default=str(lottery.get("min_join_days", 0)),
        )
        self.winners_input = ui.TextInput(
            label="中奖人数",
            required=True,
            default=str(lottery.get("winners_count", 1)),
        )
        self.repeat_input = ui.TextInput(
            label="是否允许重复中奖(是/否)",
            required=True,
            default="是" if lottery.get("allow_repeat") else "否",
        )
        self.draw_time_input = ui.TextInput(
            label="开奖时间(分钟后或MM-DD-HH:MM)",
            required=True,
            default=lottery.get("draw_time").strftime("%m-%d-%H:%M"),
        )
        self.add_item(self.roles_input)
        self.add_item(self.join_days_input)
        self.add_item(self.winners_input)
        self.add_item(self.repeat_input)
        self.add_item(self.draw_time_input)

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        try:
            min_join_days = int(self.join_days_input.value)
            winners_count = int(self.winners_input.value)
            allow_repeat = self.repeat_input.value.strip() in {"是", "yes", "YES", "y", "Y"}
            role_ids = parse_role_ids(self.roles_input.value, None)
            draw_after_minutes = None
            draw_at = None
            if self.draw_time_input.value.isdigit():
                draw_after_minutes = int(self.draw_time_input.value)
            else:
                draw_at = self.draw_time_input.value.strip()
            draw_type, draw_time = parse_draw_time(draw_after_minutes, draw_at)
            config_data = LotteryConfig(
                title=self.original_title,
                participation_mode=ParticipationMode.OPEN,
                required_role_ids=role_ids,
                min_join_days=min_join_days,
                winners_count=winners_count,
                allow_repeat=allow_repeat,
                random_mode=RandomMode.TRUE,
                draw_type=draw_type,
                draw_time=draw_time,
            )
            validate_lottery_config(config_data)
            await lottery_db.update_lottery_config(
                bot.db_pool,
                self.lottery_id,
                config_data.title,
                config_data.required_role_ids,
                config_data.min_join_days,
                config_data.winners_count,
                config_data.allow_repeat,
                config_data.random_mode.value,
                config_data.draw_type.value,
                config_data.draw_time,
            )
            await interaction.response.send_message("✅ 抽奖配置已更新。", ephemeral=True)
            if getattr(bot, "lottery_scheduler", None):
                bot.lottery_scheduler.wake_up()
        except LotteryConfigError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"❌ 修改失败: {exc}", ephemeral=True)


class PrizeDeleteSelect(ui.Select):
    def __init__(self, lottery_id: int, prizes: List[dict], page: int, per_page: int):
        start = page * per_page
        end = start + per_page
        options = [
            discord.SelectOption(label=p["prize_text"][:100], value=str(p["prize_id"]))
            for p in prizes[start:end]
        ]
        super().__init__(
            placeholder="选择要删除的奖品",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.lottery_id = lottery_id

    async def callback(self, interaction: discord.Interaction):
        prize_id = int(self.values[0])
        bot = interaction.client
        await lottery_db.delete_prize(bot.db_pool, prize_id, self.lottery_id)
        await interaction.response.send_message("✅ 奖品已删除。", ephemeral=True)
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        if interaction.message and prizes:
            view = PrizeDeleteView(self.lottery_id, prizes)
            await interaction.message.edit(view=view)
        elif interaction.message:
            await interaction.message.edit(content="✅ 当前没有奖品可删除。", view=None)


class PrizeManageView(ui.View):
    def __init__(self, lottery_id: int):
        super().__init__(timeout=300)
        self.lottery_id = lottery_id

    @ui.button(label="新增奖品", style=discord.ButtonStyle.success)
    async def add_prize(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PrizeModal(self.lottery_id))

    @ui.button(label="批量删除", style=discord.ButtonStyle.secondary)
    async def bulk_delete_prize(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        if not prizes:
            await interaction.response.send_message("⚠️ 当前没有奖品可删除。", ephemeral=True)
            return
        await interaction.response.send_modal(PrizeBulkDeleteModal(self.lottery_id, prizes))

    @ui.button(label="删除奖品", style=discord.ButtonStyle.danger)
    async def delete_prize(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        if not prizes:
            await interaction.response.send_message("⚠️ 当前没有奖品可删除。", ephemeral=True)
            return
        view = PrizeDeleteView(self.lottery_id, prizes)
        await interaction.response.send_message("选择要删除的奖品：", view=view, ephemeral=True)

    @ui.button(label="刷新", style=discord.ButtonStyle.primary)
    async def refresh_prizes(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        embed = build_prize_manage_embed(prizes)
        if interaction.message:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    @ui.button(label="刷新", style=discord.ButtonStyle.primary)
    async def refresh_prizes(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        embed = build_prize_manage_embed(prizes)
        if interaction.message:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)


class PrizeDeleteView(ui.View):
    def __init__(self, lottery_id: int, prizes: List[dict], page: int = 0):
        super().__init__(timeout=120)
        self.lottery_id = lottery_id
        self.prizes = prizes
        self.page = page
        self.per_page = 25
        self.max_page = max(0, (len(prizes) - 1) // self.per_page)
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(PrizeDeleteSelect(self.lottery_id, self.prizes, self.page, self.per_page))
        self.add_item(PrizePageButton("上一页", -1, self.page <= 0))
        self.add_item(PrizePageButton("下一页", 1, self.page >= self.max_page))


class PrizePageButton(ui.Button):
    def __init__(self, label: str, direction: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PrizeDeleteView):
            await interaction.response.defer()
            return
        new_page = max(0, min(view.max_page, view.page + self.direction))
        if new_page == view.page:
            await interaction.response.defer()
            return
        view.page = new_page
        view._build()
        await interaction.response.edit_message(view=view)


class LotteryPreviewView(ui.View):
    def __init__(self, lottery_id: int):
        super().__init__(timeout=3600)
        self.lottery_id = lottery_id

    @ui.button(label="管理奖品", style=discord.ButtonStyle.primary)
    async def manage_prizes(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        embed = build_prize_manage_embed(prizes)
        await interaction.response.send_message(embed=embed, view=PrizeManageView(self.lottery_id), ephemeral=True)

    @ui.button(label="启动抽奖", style=discord.ButtonStyle.success)
    async def activate_lottery(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        lottery = await lottery_db.get_lottery(bot.db_pool, self.lottery_id)
        if not lottery:
            await interaction.response.send_message("❌ 抽奖不存在。", ephemeral=True)
            return
        if lottery.get("creator_id") != interaction.user.id:
            await interaction.response.send_message("❌ 只有创建者可以启动抽奖。", ephemeral=True)
            return
        if lottery.get("status") != "preview":
            await interaction.response.send_message("⚠️ 当前抽奖无法启动。", ephemeral=True)
            return
        prizes = await lottery_db.list_prizes(bot.db_pool, self.lottery_id)
        if not prizes:
            await interaction.response.send_message("⚠️ 请先添加至少一个奖品。", ephemeral=True)
            return
        winners_count = int(lottery.get("winners_count", 1))
        if len(prizes) < winners_count:
            await interaction.response.send_message("⚠️ 奖品数量不足以覆盖中奖人数。", ephemeral=True)
            return
        await lottery_db.update_lottery_status(bot.db_pool, self.lottery_id, "active")
        await lottery_db.delete_preview_queue(bot.db_pool, interaction.user.id)
        await interaction.response.send_message("✅ 抽奖已启动。", ephemeral=True)
        if getattr(bot, "lottery_scheduler", None):
            bot.lottery_scheduler.wake_up()

    @ui.button(label="修改配置", style=discord.ButtonStyle.secondary)
    async def edit_lottery(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        lottery = await lottery_db.get_lottery(bot.db_pool, self.lottery_id)
        if not lottery:
            await interaction.response.send_message("❌ 抽奖不存在。", ephemeral=True)
            return
        if lottery.get("creator_id") != interaction.user.id:
            await interaction.response.send_message("❌ 只有创建者可以修改。", ephemeral=True)
            return
        if lottery.get("status") != "preview":
            await interaction.response.send_message("⚠️ 只有预览状态可修改。", ephemeral=True)
            return
        await interaction.response.send_modal(LotteryEditModal(self.lottery_id, lottery))

    @ui.button(label="关闭抽奖", style=discord.ButtonStyle.danger)
    async def cancel_lottery(self, interaction: discord.Interaction, button: ui.Button):
        bot = interaction.client
        lottery = await lottery_db.get_lottery(bot.db_pool, self.lottery_id)
        if not lottery:
            await interaction.response.send_message("❌ 抽奖不存在。", ephemeral=True)
            return
        if lottery.get("creator_id") != interaction.user.id:
            await interaction.response.send_message("❌ 只有创建者可以关闭。", ephemeral=True)
            return
        await lottery_db.update_lottery_status(bot.db_pool, self.lottery_id, "cancelled")
        await lottery_db.delete_preview_queue(bot.db_pool, interaction.user.id)
        await interaction.response.send_message("✅ 抽奖已关闭。", ephemeral=True)
        if getattr(bot, "lottery_scheduler", None):
            bot.lottery_scheduler.wake_up()


class QuizAnswerSelect(ui.Select):
    def __init__(self, lottery_id: int, question: str, options: List[str]):
        shuffled = shuffle_options(options)
        options_ui = [discord.SelectOption(label=opt[:100], value=opt) for opt in shuffled]
        super().__init__(
            placeholder="选择你的答案",
            min_values=1,
            max_values=1,
            options=options_ui,
        )
        self.lottery_id = lottery_id
        self.question = question

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        await interaction.response.defer(ephemeral=True)
        lottery = await lottery_db.get_lottery(bot.db_pool, self.lottery_id)
        if not lottery:
            await interaction.followup.send("❌ 抽奖不存在。", ephemeral=True)
            return
        if lottery.get("status") != "active":
            await interaction.followup.send("⚠️ 抽奖未在进行中。", ephemeral=True)
            return
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ 无法获取服务器信息。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        if not member:
            await interaction.followup.send("❌ 无法获取成员信息。", ephemeral=True)
            return
        required_roles = json.loads(lottery.get("required_role_ids", "[]"))
        min_join_days = int(lottery.get("min_join_days", 0))
        reason = await check_member_eligibility(guild, member, required_roles, min_join_days)
        if reason:
            await interaction.followup.send(f"❌ {reason}", ephemeral=True)
            return
        existing = await lottery_db.get_entry(bot.db_pool, self.lottery_id, interaction.user.id)
        if existing:
            if existing.get("answered_correct"):
                await interaction.followup.send("⚠️ 你已经通过答题并参与抽奖。", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ 你已经答题但未通过，无法再次参与。", ephemeral=True)
            return
        question_payload = lottery.get("question_payload")
        if isinstance(question_payload, str):
            question_payload = json.loads(question_payload)
        answer = question_payload.get("answer") if question_payload else None
        selected = self.values[0]
        is_correct = selected == answer
        await lottery_db.insert_entry(bot.db_pool, self.lottery_id, interaction.user.id, is_correct)
        await interaction.followup.send("✅ 答题完成，已记录参与。" if is_correct else "❌ 回答错误，无法参与抽奖。", ephemeral=True)


class QuizAnswerView(ui.View):
    def __init__(self, lottery_id: int, question: str, options: List[str]):
        super().__init__(timeout=300)
        self.add_item(QuizAnswerSelect(lottery_id, question, options))


class JoinLotteryButton(ui.Button):
    def __init__(self, lottery_id: int):
        super().__init__(label="参与抽奖", style=discord.ButtonStyle.success)
        self.lottery_id = lottery_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        await interaction.response.defer(ephemeral=True)
        lottery = await lottery_db.get_lottery(bot.db_pool, self.lottery_id)
        if not lottery:
            await interaction.followup.send("❌ 抽奖不存在。", ephemeral=True)
            return
        if lottery.get("status") != "active":
            await interaction.followup.send("⚠️ 抽奖未在进行中。", ephemeral=True)
            return
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ 无法获取服务器信息。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        if not member:
            await interaction.followup.send("❌ 无法获取成员信息。", ephemeral=True)
            return
        required_roles = json.loads(lottery.get("required_role_ids", "[]"))
        min_join_days = int(lottery.get("min_join_days", 0))
        reason = await check_member_eligibility(guild, member, required_roles, min_join_days)
        if reason:
            await interaction.followup.send(f"❌ {reason}", ephemeral=True)
            return
        existing = await lottery_db.get_entry(bot.db_pool, self.lottery_id, interaction.user.id)
        if existing:
            await interaction.followup.send("⚠️ 你已经参与过该抽奖。", ephemeral=True)
            return
        await lottery_db.insert_entry(bot.db_pool, self.lottery_id, interaction.user.id, True)
        await interaction.followup.send("✅ 已成功参与抽奖。", ephemeral=True)


class JoinLotteryView(ui.View):
    def __init__(self, lottery_id: int):
        super().__init__(timeout=None)
        self.add_item(JoinLotteryButton(lottery_id))
