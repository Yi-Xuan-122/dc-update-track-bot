from __future__ import annotations
import asyncio
import datetime
import logging
from typing import Dict, List, Optional
import discord
from src import config
from src.lottery import db as lottery_db
from src.lottery.models import LotteryStatus
from src.lottery.service import draw_winners

log = logging.getLogger(__name__)

class LotteryScheduler:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()

    def start(self):
        if not self.task or self.task.done():
            self.task = self.bot.loop.create_task(self._run())

    def wake_up(self):
        self._wake_event.set()

    async def _run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._process_due_draws()
                await self._process_expired_previews()
            except Exception as exc:
                log.error("LotteryScheduler error: %s", exc)
            wait_seconds = await self._compute_next_wait()
            try:
                self._wake_event.clear()
                await asyncio.wait_for(self._wake_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                continue

    async def _compute_next_wait(self) -> float:
        now = datetime.datetime.now(config.UTC_PLUS_8)
        next_draw = await lottery_db.get_next_draw_time(self.bot.db_pool)
        next_preview = await lottery_db.get_next_preview_expire(self.bot.db_pool)
        candidates = [t for t in [next_draw, next_preview] if t]
        if not candidates:
            return 300.0
        next_time = min(candidates)
        delta = (next_time - now).total_seconds()
        if delta < 1:
            return 1.0
        return min(delta, 3600.0)

    async def _process_expired_previews(self):
        now = datetime.datetime.now(config.UTC_PLUS_8)
        expired = await lottery_db.list_expired_previews(self.bot.db_pool, now)
        for lottery in expired:
            try:
                await lottery_db.update_lottery_status(self.bot.db_pool, lottery["lottery_id"], LotteryStatus.CANCELLED.value)
                await lottery_db.delete_preview_queue(self.bot.db_pool, lottery["creator_id"])
            except Exception as exc:
                log.error("Failed to expire preview %s: %s", lottery["lottery_id"], exc)

    async def _process_due_draws(self):
        now = datetime.datetime.now(config.UTC_PLUS_8)
        due = await lottery_db.list_due_draws(self.bot.db_pool, now)
        for lottery in due:
            lottery_id = lottery["lottery_id"]
            try:
                await lottery_db.update_lottery_status(self.bot.db_pool, lottery_id, LotteryStatus.DRAWING.value)
                await self._draw_and_notify(lottery)
                await lottery_db.update_lottery_status(self.bot.db_pool, lottery_id, LotteryStatus.FINISHED.value)
            except Exception as exc:
                log.error("Failed to draw lottery %s: %s", lottery_id, exc)

    async def _draw_and_notify(self, lottery: Dict):
        guild = self.bot.get_guild(lottery["guild_id"])
        channel = self.bot.get_channel(lottery["channel_id"])
        if not guild or not channel:
            return
        prizes = await lottery_db.list_prizes(self.bot.db_pool, lottery["lottery_id"])
        if not prizes:
            await channel.send("⚠️ 抽奖已到期，但未配置奖品。")
            return
        winners = await draw_winners(
            self.bot.db_pool,
            lottery["lottery_id"],
            bool(lottery["allow_repeat"]),
            int(lottery["winners_count"]),
            lottery.get("random_mode", "true"),
        )
        if not winners:
            await channel.send("⚠️ 抽奖到期，但没有符合条件的参与者。")
            return
        await self._notify_winners(channel, lottery, winners, prizes)

    async def _notify_winners(self, channel: discord.TextChannel, lottery: Dict, winners: List[int], prizes: List[Dict]):
        prize_list = prizes[:]
        result_lines: List[str] = []
        for idx, user_id in enumerate(winners):
            prize = prize_list[idx % len(prize_list)]
            await lottery_db.insert_winner(self.bot.db_pool, lottery["lottery_id"], user_id, prize["prize_id"])
            result_lines.append(f"<@{user_id}> -> {prize['prize_text']}")
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(f"🎉 你在抽奖『{lottery.get('title') or '抽奖活动'}』中获奖！\n奖品：{prize['prize_text']}")
            except Exception:
                pass
        embed = discord.Embed(
            title=f"抽奖结果 - {lottery.get('title') or '抽奖活动'}",
            description="\n".join(result_lines),
            color=discord.Color.gold(),
        )
        await channel.send(embed=embed)
