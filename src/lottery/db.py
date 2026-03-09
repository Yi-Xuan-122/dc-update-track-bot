from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional
import datetime
import aiomysql

log = logging.getLogger(__name__)

async def setup_lottery_tables(pool: aiomysql.pool.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lottery_events (
                    lottery_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    guild_id BIGINT UNSIGNED NOT NULL,
                    channel_id BIGINT UNSIGNED NOT NULL,
                    creator_id BIGINT UNSIGNED NOT NULL,
                    title VARCHAR(200) DEFAULT NULL,
                    status VARCHAR(32) NOT NULL,
                    participation_mode VARCHAR(32) NOT NULL,
                    required_role_ids JSON NOT NULL,
                    min_join_days INT NOT NULL,
                    winners_count INT NOT NULL,
                    allow_repeat BOOLEAN NOT NULL DEFAULT FALSE,
                    random_mode VARCHAR(16) NOT NULL DEFAULT 'true',
                    draw_type VARCHAR(32) NOT NULL,
                    draw_time DATETIME NOT NULL,
                    preview_expires_at DATETIME NOT NULL,
                    question_payload JSON DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                );
            """)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lottery_prizes (
                    prize_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    lottery_id BIGINT UNSIGNED NOT NULL,
                    prize_text VARCHAR(200) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lottery_id) REFERENCES lottery_events(lottery_id) ON DELETE CASCADE
                );
            """)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lottery_entries (
                    entry_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    lottery_id BIGINT UNSIGNED NOT NULL,
                    user_id BIGINT UNSIGNED NOT NULL,
                    answered_correct BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_entry (lottery_id, user_id),
                    FOREIGN KEY (lottery_id) REFERENCES lottery_events(lottery_id) ON DELETE CASCADE
                );
            """)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lottery_winners (
                    winner_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    lottery_id BIGINT UNSIGNED NOT NULL,
                    user_id BIGINT UNSIGNED NOT NULL,
                    prize_id BIGINT UNSIGNED NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lottery_id) REFERENCES lottery_events(lottery_id) ON DELETE CASCADE,
                    FOREIGN KEY (prize_id) REFERENCES lottery_prizes(prize_id) ON DELETE CASCADE
                );
            """)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lottery_preview_queue (
                    creator_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    lottery_id BIGINT UNSIGNED NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await cursor.execute("""
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'lottery_events'
                  AND COLUMN_NAME = 'random_mode'
            """)
            if (await cursor.fetchone())[0] == 0:
                await cursor.execute("ALTER TABLE lottery_events ADD COLUMN random_mode VARCHAR(16) NOT NULL DEFAULT 'true';")
        await conn.commit()


async def insert_lottery(pool: aiomysql.pool.Pool, payload: Dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO lottery_events (
                    lottery_id, guild_id, channel_id, creator_id, title, status,
                    participation_mode, required_role_ids, min_join_days, winners_count,
                    allow_repeat, random_mode, draw_type, draw_time, preview_expires_at, question_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    payload["lottery_id"], payload["guild_id"], payload["channel_id"],
                    payload["creator_id"], payload.get("title"), payload["status"],
                    payload["participation_mode"], json.dumps(payload["required_role_ids"]),
                    payload["min_join_days"], payload["winners_count"], payload["allow_repeat"],
                    payload["random_mode"], payload["draw_type"], payload["draw_time"], payload["preview_expires_at"],
                    json.dumps(payload.get("question_payload")) if payload.get("question_payload") else None,
                ),
            )
        await conn.commit()


async def update_lottery_status(pool: aiomysql.pool.Pool, lottery_id: int, status: str) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE lottery_events SET status = %s WHERE lottery_id = %s",
                (status, lottery_id),
            )
        await conn.commit()


async def update_lottery_config(
    pool: aiomysql.pool.Pool,
    lottery_id: int,
    title: Optional[str],
    required_role_ids: List[int],
    min_join_days: int,
    winners_count: int,
    allow_repeat: bool,
    random_mode: str,
    draw_type: str,
    draw_time: datetime.datetime,
) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE lottery_events
                SET title = %s,
                    required_role_ids = %s,
                    min_join_days = %s,
                    winners_count = %s,
                    allow_repeat = %s,
                    random_mode = %s,
                    draw_type = %s,
                    draw_time = %s
                WHERE lottery_id = %s
                """,
                (
                    title,
                    json.dumps(required_role_ids),
                    min_join_days,
                    winners_count,
                    allow_repeat,
                    random_mode,
                    draw_type,
                    draw_time,
                    lottery_id,
                ),
            )
        await conn.commit()


async def delete_preview_queue(pool: aiomysql.pool.Pool, creator_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM lottery_preview_queue WHERE creator_id = %s",
                (creator_id,),
            )
        await conn.commit()


async def insert_preview_queue(pool: aiomysql.pool.Pool, creator_id: int, lottery_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO lottery_preview_queue (creator_id, lottery_id) VALUES (%s, %s)",
                (creator_id, lottery_id),
            )
        await conn.commit()


async def get_preview_queue_count(pool: aiomysql.pool.Pool) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM lottery_preview_queue")
            result = await cursor.fetchone()
            return int(result[0]) if result else 0


async def insert_prize(pool: aiomysql.pool.Pool, lottery_id: int, prize_text: str) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO lottery_prizes (lottery_id, prize_text) VALUES (%s, %s)",
                (lottery_id, prize_text),
            )
        await conn.commit()


async def delete_prize(pool: aiomysql.pool.Pool, prize_id: int, lottery_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM lottery_prizes WHERE prize_id = %s AND lottery_id = %s",
                (prize_id, lottery_id),
            )
        await conn.commit()


async def delete_prizes_by_ids(pool: aiomysql.pool.Pool, lottery_id: int, prize_ids: List[int]) -> None:
    if not prize_ids:
        return
    placeholders = ",".join(["%s"] * len(prize_ids))
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"DELETE FROM lottery_prizes WHERE lottery_id = %s AND prize_id IN ({placeholders})",
                (lottery_id, *prize_ids),
            )
        await conn.commit()


async def list_prizes(pool: aiomysql.pool.Pool, lottery_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT prize_id, prize_text FROM lottery_prizes WHERE lottery_id = %s ORDER BY prize_id",
                (lottery_id,),
            )
            return await cursor.fetchall()


async def count_prizes(pool: aiomysql.pool.Pool, lottery_id: int) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) FROM lottery_prizes WHERE lottery_id = %s",
                (lottery_id,),
            )
            result = await cursor.fetchone()
            return int(result[0]) if result else 0


async def insert_entry(pool: aiomysql.pool.Pool, lottery_id: int, user_id: int, answered_correct: bool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO lottery_entries (lottery_id, user_id, answered_correct)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE answered_correct = VALUES(answered_correct)
                """,
                (lottery_id, user_id, answered_correct),
            )
        await conn.commit()


async def list_entries(pool: aiomysql.pool.Pool, lottery_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT user_id, answered_correct FROM lottery_entries WHERE lottery_id = %s",
                (lottery_id,),
            )
            return await cursor.fetchall()


async def get_entry(pool: aiomysql.pool.Pool, lottery_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT user_id, answered_correct FROM lottery_entries WHERE lottery_id = %s AND user_id = %s",
                (lottery_id, user_id),
            )
            return await cursor.fetchone()


async def count_entries(pool: aiomysql.pool.Pool, lottery_id: int) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) FROM lottery_entries WHERE lottery_id = %s",
                (lottery_id,),
            )
            result = await cursor.fetchone()
            return int(result[0]) if result else 0


async def insert_winner(pool: aiomysql.pool.Pool, lottery_id: int, user_id: int, prize_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO lottery_winners (lottery_id, user_id, prize_id) VALUES (%s, %s, %s)",
                (lottery_id, user_id, prize_id),
            )
        await conn.commit()


async def get_lottery(pool: aiomysql.pool.Pool, lottery_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT * FROM lottery_events WHERE lottery_id = %s", (lottery_id,))
            return await cursor.fetchone()


async def list_active_lotteries(pool: aiomysql.pool.Pool) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT * FROM lottery_events WHERE status IN ('active','preview')"
            )
            return await cursor.fetchall()


async def list_due_draws(pool: aiomysql.pool.Pool, now: datetime.datetime) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT * FROM lottery_events WHERE status = 'active' AND draw_time <= %s",
                (now,),
            )
            return await cursor.fetchall()


async def list_expired_previews(pool: aiomysql.pool.Pool, now: datetime.datetime) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT * FROM lottery_events WHERE status = 'preview' AND preview_expires_at <= %s",
                (now,),
            )
            return await cursor.fetchall()


async def get_next_draw_time(pool: aiomysql.pool.Pool) -> Optional[datetime.datetime]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT MIN(draw_time) FROM lottery_events WHERE status = 'active'"
            )
            result = await cursor.fetchone()
            return result[0] if result and result[0] else None


async def get_next_preview_expire(pool: aiomysql.pool.Pool) -> Optional[datetime.datetime]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT MIN(preview_expires_at) FROM lottery_events WHERE status = 'preview'"
            )
            result = await cursor.fetchone()
            return result[0] if result and result[0] else None


async def get_preview_by_creator(pool: aiomysql.pool.Pool, creator_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT * FROM lottery_preview_queue WHERE creator_id = %s",
                (creator_id,),
            )
            return await cursor.fetchone()
