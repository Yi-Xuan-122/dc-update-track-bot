from __future__ import annotations
import datetime
import logging
import random
import re
from typing import Any, Dict, List, Optional
import httpx
import discord
from src.lottery.models import LotteryConfig, LotteryConfigError, ParticipationMode, LotteryStatus, RandomMode, can_transition
from src.llm import LLM
from src import config
from src.lottery import db as lottery_db

log = logging.getLogger(__name__)

RANDOM_API_URL = "https://www.random.org/integers/?num=1&min={min}&max={max}&col=1&base=10&format=plain&rnd=new&cl=w"

async def fetch_random_number(min_value: int, max_value: int) -> int:
    url = RANDOM_API_URL.format(min=min_value, max=max_value)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"随机数 API 请求失败: {response.status_code}")
    match = re.search(r"(-?\d+)", response.text)
    if not match:
        raise RuntimeError("随机数 API 返回无效")
    return int(match.group(1))


def fetch_pseudo_random(min_value: int, max_value: int) -> int:
    return random.randint(min_value, max_value)


def shuffle_options(options: List[str]) -> List[str]:
    options_copy = options[:]
    random.shuffle(options_copy)
    return options_copy


async def generate_quiz_question() -> Dict[str, Any]:
    llm = LLM()
    prompt = """
请生成一个随机方向的单选题，返回严格 JSON：
{
  "question": "问题",
  "options": ["A选项", "B选项", "C选项", "D选项"],
  "answer": "正确选项内容"
}
要求：options 必须为 4 个字符串，answer 必须等于 options 中某一项。
""".strip()
    payload = [{"role": "user", "content": prompt}]
    result = await llm.llm_call(json_dump_payload(payload))
    text = result.get("text", "")
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise LotteryConfigError("LLM 返回格式错误，无法解析题目。")
    data = safe_json_load(match.group(0))
    if not data:
        raise LotteryConfigError("LLM 返回格式错误，无法解析题目。")
    options = data.get("options", [])
    answer = data.get("answer")
    if not isinstance(options, list) or len(options) != 4:
        raise LotteryConfigError("LLM 题目选项无效。")
    if answer not in options:
        raise LotteryConfigError("LLM 题目答案无效。")
    return {
        "question": data.get("question", ""),
        "options": options,
        "answer": answer,
    }


def json_dump_payload(payload: List[Dict[str, Any]]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False)


def safe_json_load(text: str) -> Optional[Dict[str, Any]]:
    import json
    try:
        return json.loads(text)
    except Exception:
        return None


async def check_member_eligibility(
    guild: discord.Guild,
    member: discord.Member,
    required_role_ids: List[int],
    min_join_days: int,
) -> Optional[str]:
    if required_role_ids:
        role_ids = {role.id for role in member.roles}
        if not any(role_id in role_ids for role_id in required_role_ids):
            return "没有符合要求的身份组，无法参与抽奖。"
    if min_join_days > 0:
        if not member.joined_at:
            return "无法获取加入时间，无法参与抽奖。"
        utc8 = config.UTC_PLUS_8
        join_time = member.joined_at.astimezone(utc8)
        now = datetime.datetime.now(utc8)
        if (now - join_time).days < min_join_days:
            return f"加入服务器时间不足 {min_join_days} 天，无法参与抽奖。"
    return None


async def draw_winners(
    pool,
    lottery_id: int,
    allow_repeat: bool,
    winners_count: int,
    random_mode: str,
) -> List[int]:
    entries = await lottery_db.list_entries(pool, lottery_id)
    eligible = [entry["user_id"] for entry in entries if entry["answered_correct"]]
    if not eligible:
        return []
    winners: List[int] = []
    pool_ids = eligible[:]
    true_random_failed = False
    for _ in range(winners_count):
        if not pool_ids:
            break
        if random_mode == RandomMode.TRUE.value and not true_random_failed:
            success = False
            for _ in range(10):
                try:
                    idx = await fetch_random_number(1, len(pool_ids)) - 1
                    success = True
                    break
                except Exception as exc:
                    log.warning("真随机请求失败: %s", exc)
            if not success:
                true_random_failed = True
        if random_mode != RandomMode.TRUE.value or true_random_failed:
            idx = fetch_pseudo_random(1, len(pool_ids)) - 1
        winners.append(pool_ids[idx])
        if not allow_repeat:
            pool_ids.pop(idx)
    return winners


def ensure_transition(current: str, target: str) -> None:
    current_status = LotteryStatus(current)
    target_status = LotteryStatus(target)
    if not can_transition(current_status, target_status):
        raise LotteryConfigError(f"不允许从 {current_status.value} 切换到 {target_status.value}")
