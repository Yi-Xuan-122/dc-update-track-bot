from __future__ import annotations
import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional
import discord
from src import config
from src.lottery import db as lottery_db
from src.lottery.models import LotteryStatus
from src.lottery.service import draw_winners, has_sufficient_prize_count

log = logging.getLogger(__name__)


def normalize_scheduler_time(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=config.UTC_PLUS_8)
    return value.astimezone(config.UTC_PLUS_8)

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

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning("无法获取抽奖频道 %s: %s", channel_id, exc)
            return None

    async def _run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            wait_seconds = 300.0
            try:
                await self._process_due_draws()
                await self._process_expired_previews()
                wait_seconds = await self._compute_next_wait()
                log.debug("LotteryScheduler 下一次唤醒等待 %.2f 秒。", wait_seconds)
            except Exception:
                log.exception("LotteryScheduler 主循环发生异常。")
                wait_seconds = 5.0
            try:
                self._wake_event.clear()
                await asyncio.wait_for(self._wake_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                continue

    async def _compute_next_wait(self) -> float:
        now = datetime.datetime.now(config.UTC_PLUS_8)
        next_draw = normalize_scheduler_time(await lottery_db.get_next_draw_time(self.bot.db_pool))
        next_preview = normalize_scheduler_time(await lottery_db.get_next_preview_expire(self.bot.db_pool))
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
        if due:
            log.info("检测到 %s 个到期开奖。", len(due))
        for lottery in due:
            lottery_id = lottery["lottery_id"]
            try:
                await lottery_db.update_lottery_status(self.bot.db_pool, lottery_id, LotteryStatus.DRAWING.value)
                await self._draw_and_notify(lottery)
                await lottery_db.update_lottery_status(self.bot.db_pool, lottery_id, LotteryStatus.FINISHED.value)
            except Exception:
                log.exception("抽奖 %s 开奖失败。", lottery_id)
                try:
                    await lottery_db.update_lottery_status(self.bot.db_pool, lottery_id, LotteryStatus.ACTIVE.value)
                except Exception:
                    log.exception("抽奖 %s 状态回滚失败。", lottery_id)

    async def _draw_and_notify(self, lottery: Dict):
        lottery_id = int(lottery["lottery_id"])
        channel = await self._resolve_channel(int(lottery["channel_id"]))
        if not channel:
            raise RuntimeError(f"无法获取开奖频道: {lottery['channel_id']}")
        log.info("开始处理抽奖 %s 的开奖。", lottery_id)
        prizes = await lottery_db.list_prizes(self.bot.db_pool, lottery["lottery_id"])
        if not prizes:
            await channel.send("⚠️ 抽奖已到期，但未配置奖品。")
            return
        if not has_sufficient_prize_count(len(prizes), int(lottery["winners_count"])):
            await channel.send("⚠️ 抽奖配置无效：奖品数量必须大于或等于中奖人数。")
            return
        winners, used_pseudo_fallback = await draw_winners(
            self.bot.db_pool,
            lottery["lottery_id"],
            bool(lottery["allow_repeat"]),
            int(lottery["winners_count"]),
            lottery.get("random_mode", "true"),
        )
        if not winners:
            await channel.send("⚠️ 抽奖到期，但没有符合条件的参与者。")
            await self._notify_creator_draw_summary(lottery, [], prizes)
            return
        await self._notify_winners(channel, lottery, winners, prizes, used_pseudo_fallback)

    async def _notify_winners(self, channel: Any, lottery: Dict, winners: List[int], prizes: List[Dict], used_pseudo_fallback: bool):
        prize_list = prizes[:]
        result_lines: List[str] = []
        creator_summary_items: List[Dict[str, Any]] = []
        creator_mention = f"<@{lottery['creator_id']}>"
        prize_hosting_enabled = bool(lottery.get("prize_hosting_enabled"))
        for idx, user_id in enumerate(winners):
            prize = prize_list[idx]
            await lottery_db.insert_winner(self.bot.db_pool, lottery["lottery_id"], user_id, prize["prize_id"])
            public_prize_text = prize["prize_text"]
            dm_prize_text = prize["prize_text"]
            if prize_hosting_enabled:
                public_prize_text = "托管奖品（已通过私信发放）"
            else:
                dm_prize_text = f"{prize['prize_text']}\n兑奖请私聊发起者：{creator_mention}"
            creator_summary_items.append({"user_id": user_id, "prize_text": prize["prize_text"]})
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(f"🎉 你在抽奖『{lottery.get('title') or '抽奖活动'}』中获奖！\n奖品：{dm_prize_text}")
            except Exception:
                if prize_hosting_enabled:
                    public_prize_text = f"托管奖品（私信发送失败，请联系发起者 {creator_mention}）"
            result_lines.append(f"<@{user_id}> -> {public_prize_text}")
        unsent_prizes = prize_list[len(creator_summary_items):]
        embed = discord.Embed(
            title=f"抽奖结果 - {lottery.get('title') or '抽奖活动'}",
            description="\n".join(result_lines),
            color=discord.Color.gold(),
        )
        if used_pseudo_fallback:
            embed.add_field(name="随机说明", value="本次真随机请求连续失败 5 次，已自动切换为伪随机完成开奖。", inline=False)
        if prize_hosting_enabled:
            embed.set_footer(text="托管奖品内容不会在频道中公开显示。")
        await channel.send(embed=embed)
        await self._notify_creator_draw_summary(lottery, creator_summary_items, unsent_prizes, used_pseudo_fallback)

    async def _notify_creator_draw_summary(
        self,
        lottery: Dict,
        creator_summary_items: List[Dict[str, Any]],
        unsent_prizes: List[Dict],
        used_pseudo_fallback: bool = False,
    ) -> None:
        creator_id = int(lottery.get("creator_id", 0))
        if not creator_id:
            return

        try:
            creator = self.bot.get_user(creator_id) or await self.bot.fetch_user(creator_id)
        except Exception as exc:
            log.warning("无法获取抽奖发起者 %s 的用户信息: %s", creator_id, exc)
            return

        lines: List[str] = [f"你的抽奖『{lottery.get('title') or '抽奖活动'}』已结束。"]

        if creator_summary_items:
            for item in creator_summary_items:
                lines.append(f"你的抽奖中，{item['prize_text']}中奖者：<@{item['user_id']}>。")
        else:
            lines.append("你的抽奖中，本次没有中奖者。")

        if unsent_prizes:
            unsent_text = "、".join(str(prize.get("prize_text", "")).strip() for prize in unsent_prizes if str(prize.get("prize_text", "")).strip())
            if unsent_text:
                lines.append(f"部分奖品因参与人数不够未能送出：{unsent_text}")

        if used_pseudo_fallback:
            lines.append("本次开奖中，真随机请求连续失败 5 次，系统已自动切换为伪随机完成开奖。")

        try:
            await creator.send("\n".join(lines))
        except Exception as exc:
            log.warning("无法向抽奖发起者 %s 发送开奖结果摘要: %s", creator_id, exc)
