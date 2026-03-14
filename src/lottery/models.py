from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import datetime
import re
from src import config


class LotteryConfigError(ValueError):
    pass


class LotteryStatus(str, Enum):
    PREVIEW = "preview"
    ACTIVE = "active"
    DRAWING = "drawing"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class ParticipationMode(str, Enum):
    OPEN = "open"
    QUIZ = "quiz"


class RandomMode(str, Enum):
    TRUE = "true"
    PSEUDO = "pseudo"


class DrawTimeType(str, Enum):
    DURATION = "duration"
    FIXED = "fixed"


@dataclass
class LotteryConfig:
    title: Optional[str]
    participation_mode: ParticipationMode
    required_role_ids: List[int]
    min_join_days: int
    winners_count: int
    allow_repeat: bool
    random_mode: RandomMode
    draw_type: DrawTimeType
    draw_time: datetime.datetime
    draw_duration_minutes: Optional[int] = None


LOTTERY_STATE_TRANSITIONS = {
    LotteryStatus.PREVIEW: {LotteryStatus.ACTIVE, LotteryStatus.CANCELLED},
    LotteryStatus.ACTIVE: {LotteryStatus.DRAWING, LotteryStatus.CANCELLED},
    LotteryStatus.DRAWING: {LotteryStatus.FINISHED},
    LotteryStatus.FINISHED: set(),
    LotteryStatus.CANCELLED: set(),
}


def can_transition(current: LotteryStatus, target: LotteryStatus) -> bool:
    return target in LOTTERY_STATE_TRANSITIONS.get(current, set())


def parse_role_ids(role_text: Optional[str], default_role_id: Optional[int]) -> List[int]:
    if role_text:
        role_ids = [int(rid) for rid in re.findall(r"\d+", role_text)]
    else:
        role_ids = []
    if not role_ids and default_role_id:
        role_ids = [default_role_id]
    return list(dict.fromkeys(role_ids))


def normalize_lottery_time(draw_time: datetime.datetime) -> datetime.datetime:
    if draw_time.tzinfo is None:
        return draw_time.replace(tzinfo=config.UTC_PLUS_8)
    return draw_time.astimezone(config.UTC_PLUS_8)


def format_lottery_draw_time(draw_time: datetime.datetime) -> str:
    normalized = normalize_lottery_time(draw_time)
    return normalized.strftime("%Y-%m-%d %H:%M UTC+8")


def build_draw_time_preview_text(
    draw_type: DrawTimeType | str,
    draw_time: datetime.datetime,
    draw_duration_minutes: Optional[int],
) -> str:
    draw_type_value = draw_type.value if isinstance(draw_type, DrawTimeType) else str(draw_type)
    if draw_type_value == DrawTimeType.DURATION.value:
        if draw_duration_minutes:
            return f"以第一次执行 /lottery 开始 的时间为准，{draw_duration_minutes} 分钟后开奖"
        return "以第一次执行 /lottery 开始 的时间为准开奖"
    return format_lottery_draw_time(draw_time)


def parse_draw_time(
    draw_after_minutes: Optional[int],
    draw_at_str: Optional[str],
) -> tuple[DrawTimeType, datetime.datetime, Optional[int]]:
    utc8 = config.UTC_PLUS_8
    now = datetime.datetime.now(utc8)
    if draw_after_minutes is not None and draw_at_str is not None:
        raise LotteryConfigError("开奖时间只能选择一种方式。")
    if draw_after_minutes is None and draw_at_str is None:
        raise LotteryConfigError("必须设置开奖时间或开奖时长。")
    if draw_after_minutes is not None:
        if draw_after_minutes < 3 or draw_after_minutes > 7 * 24 * 60:
            raise LotteryConfigError("开奖时长必须在 3 分钟到 7 天之间。")
        return DrawTimeType.DURATION, now + datetime.timedelta(minutes=draw_after_minutes), draw_after_minutes
    if not re.fullmatch(r"\d{2}-\d{2}-\d{2}:\d{2}", draw_at_str or ""):
        raise LotteryConfigError("开奖时间格式必须为 MM-DD-HH:MM，例如 02-24-12:00。")
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2}):(\d{2})", draw_at_str)
    if not match:
        raise LotteryConfigError("开奖时间格式必须为 MM-DD-HH:MM，例如 02-24-12:00。")
    month, day, hour, minute = map(int, match.groups())
    try:
        draw_time = datetime.datetime(now.year, month, day, hour, minute, tzinfo=utc8)
    except ValueError as exc:
        raise LotteryConfigError("开奖时间无效，请检查日期。") from exc
    if draw_time <= now:
        raise LotteryConfigError("开奖时间必须晚于当前时间。")
    return DrawTimeType.FIXED, draw_time, None


def validate_lottery_config(config_data: LotteryConfig) -> None:
    if config_data.min_join_days < 0:
        raise LotteryConfigError("加入天数不能为负数。")
    if config_data.winners_count <= 0:
        raise LotteryConfigError("中奖人数必须大于 0。")


def validate_prize_text(prize_text: str) -> str:
    if not prize_text or not prize_text.strip():
        raise LotteryConfigError("奖品内容不能为空。")
    cleaned = prize_text.strip()
    if len(cleaned) > 200:
        raise LotteryConfigError("奖品内容不能超过 200 个字符。")
    return cleaned
