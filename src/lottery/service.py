from __future__ import annotations
import datetime
import html
import json
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

RANDOM_API_URL = "https://www.random.org/integers/?num=1&min={min}&max={max}&col=1&base=10&format=plain&rnd=new"


def parse_random_number_response(response_text: str) -> int:
    decoded = html.unescape(response_text or "").strip()

    for line in decoded.splitlines():
        candidate = line.strip()
        if re.fullmatch(r"-?\d+", candidate):
            return int(candidate)

    text_without_tags = re.sub(r"<[^>]+>", "\n", decoded)
    for line in text_without_tags.splitlines():
        candidate = line.strip()
        if re.fullmatch(r"-?\d+", candidate):
            return int(candidate)

    raise RuntimeError(f"随机数 API 返回无效: {decoded[:200]}")

async def fetch_random_number(min_value: int, max_value: int) -> int:
    url = RANDOM_API_URL.format(min=min_value, max=max_value)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"随机数 API 请求失败: {response.status_code}, body={response.text[:200]}")

    value = parse_random_number_response(response.text)
    if value < min_value or value > max_value:
        raise RuntimeError(
            f"随机数 API 返回越界值: {value}，期望范围 {min_value}~{max_value}"
        )
    return value


def fetch_pseudo_random(min_value: int, max_value: int) -> int:
    return random.randint(min_value, max_value)


def shuffle_options(options: List[str]) -> List[str]:
    options_copy = options[:]
    random.shuffle(options_copy)
    return options_copy


def has_sufficient_prize_count(prize_count: int, winners_count: int) -> bool:
    return prize_count >= winners_count


QUIZ_GENERATION_SYSTEM_PROMPT = (
    "你是一个只输出 JSON 的出题助手。"
    "不要输出解释，不要输出代码块，不要输出额外文字。"
)


QUIZ_GENERATION_USER_PROMPT = """
请生成一道随机方向的中文单选题。

返回格式必须是严格 JSON 对象：
{
  "question": "题目",
  "options": ["选项1", "选项2", "选项3", "选项4"],
  "answer": "正确选项内容"
}

要求：
1. options 必须恰好有 4 个字符串。
2. 4 个选项不能重复。
3. answer 必须与 options 中某一项完全一致。
4. 不要输出 Markdown 代码块。
5. 不要输出 JSON 之外的任何内容。
""".strip()


def build_quiz_generation_payload() -> List[Dict[str, Any]]:
    if config.LLM_FORMAT == "gemini":
        return [
            {
                "systemInstruction": {
                    "parts": [{"text": QUIZ_GENERATION_SYSTEM_PROMPT}]
                }
            },
            {
                "role": "user",
                "parts": [{"text": QUIZ_GENERATION_USER_PROMPT}]
            },
        ]
    return [
        {"role": "system", "content": QUIZ_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": QUIZ_GENERATION_USER_PROMPT},
    ]


def extract_json_object_text(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    cleaned = text.strip()
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    brace_match = re.search(r"\{[\s\S]*\}", cleaned)
    if brace_match:
        return brace_match.group(0).strip()
    return None


def normalize_quiz_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    question = str(data.get("question", "")).strip()
    if not question:
        raise LotteryConfigError("LLM 题目内容为空。")

    options_raw = data.get("options")
    if not isinstance(options_raw, list) or len(options_raw) != 4:
        raise LotteryConfigError("LLM 题目选项无效。")

    options = []
    for option in options_raw:
        if not isinstance(option, str):
            raise LotteryConfigError("LLM 题目选项无效。")
        cleaned_option = option.strip()
        if not cleaned_option:
            raise LotteryConfigError("LLM 题目选项无效。")
        options.append(cleaned_option)

    if len(set(options)) != 4:
        raise LotteryConfigError("LLM 题目选项存在重复。")

    answer = data.get("answer")
    if not isinstance(answer, str) or answer.strip() not in options:
        raise LotteryConfigError("LLM 题目答案无效。")

    return {"question": question, "options": options, "answer": answer.strip()}


async def generate_quiz_question() -> Dict[str, Any]:
    llm = LLM()
    payload_json = json.dumps(build_quiz_generation_payload(), ensure_ascii=False)
    last_error: Optional[Exception] = None

    for attempt in range(3):
        try:
            result = await llm.llm_call(payload_json)
            text = result.get("text", "")
            json_text = extract_json_object_text(text)
            if not json_text:
                raise LotteryConfigError("LLM 返回格式错误，无法解析题目。")
            data = safe_json_load(json_text)
            if not data:
                raise LotteryConfigError("LLM 返回格式错误，无法解析题目。")
            return normalize_quiz_payload(data)
        except Exception as exc:
            last_error = exc
            log.warning("生成抽奖题目失败，第 %s 次重试: %s", attempt + 1, exc)

    raise LotteryConfigError("生成题目失败，请稍后重试。") from last_error


def safe_json_load(text: str) -> Optional[Dict[str, Any]]:
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
) -> tuple[List[int], bool]:
    entries = await lottery_db.list_entries(pool, lottery_id)
    eligible = [entry["user_id"] for entry in entries if entry["answered_correct"]]
    if not eligible:
        return [], False
    winners: List[int] = []
    pool_ids = eligible[:]
    true_random_failed = False
    used_pseudo_fallback = False
    for _ in range(winners_count):
        if not pool_ids:
            break
        idx: Optional[int] = None
        if random_mode == RandomMode.TRUE.value and not true_random_failed:
            if len(pool_ids) == 1:
                idx = 0
            else:
                success = False
                for _ in range(5):
                    try:
                        candidate_idx = await fetch_random_number(1, len(pool_ids)) - 1
                        if not 0 <= candidate_idx < len(pool_ids):
                            raise RuntimeError(f"真随机索引越界: {candidate_idx}")
                        idx = candidate_idx
                        success = True
                        break
                    except Exception as exc:
                        log.warning("真随机请求失败: %s", exc)
                if not success:
                    true_random_failed = True
                    used_pseudo_fallback = True
                    log.warning("真随机请求连续失败 5 次，已切换为伪随机完成后续开奖。")
        if idx is None:
            if random_mode == RandomMode.TRUE.value and true_random_failed:
                used_pseudo_fallback = True
            idx = fetch_pseudo_random(1, len(pool_ids)) - 1
        if not 0 <= idx < len(pool_ids):
            log.warning("随机索引越界，改用本地兜底随机。idx=%s, pool_size=%s", idx, len(pool_ids))
            idx = random.randrange(len(pool_ids))
            used_pseudo_fallback = True
        winners.append(pool_ids[idx])
        if not allow_repeat:
            pool_ids.pop(idx)
    return winners, used_pseudo_fallback


def ensure_transition(current: str, target: str) -> None:
    current_status = LotteryStatus(current)
    target_status = LotteryStatus(target)
    if not can_transition(current_status, target_status):
        raise LotteryConfigError(f"不允许从 {current_status.value} 切换到 {target_status.value}")
