import aiomysql
import logging
log = logging.getLogger(__name__)
DEFAULT_GUILD_ID = 1134557553011998840
GUILD_ID_MIGRATION_KEY = "guild_id_backfill_v1"

async def setup_database(pool: aiomysql.pool.Pool):
    """检查并创建所有需要的数据库表"""
    logging.info("正在检查并创建数据库表...")
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 表1: 用户信息与状态表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        track_new_thread BOOLEAN NOT NULL DEFAULT TRUE
                    );
                """)
                # 兼容旧表，检查列是否存在
                await cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'track_new_thread'
                """)
                if (await cursor.fetchone())[0] == 0:
                    await cursor.execute("ALTER TABLE users ADD COLUMN track_new_thread BOOLEAN NOT NULL DEFAULT TRUE;")

                # 表2: 被管理的帖子表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS managed_threads (
                        thread_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        guild_id BIGINT UNSIGNED NOT NULL,
                        author_id BIGINT UNSIGNED NOT NULL,
                        last_update_url VARCHAR(255) DEFAULT NULL,
                        last_update_message TEXT DEFAULT NULL,
                        last_update_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
                        last_update_type ENUM('release', 'test') DEFAULT NULL,
                        FOREIGN KEY (author_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                """)
                # 表3: 帖子订阅表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS thread_subscriptions (
                        subscription_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT UNSIGNED NOT NULL,
                        thread_id BIGINT UNSIGNED NOT NULL,
                        guild_id BIGINT UNSIGNED NOT NULL,
                        subscribe_release BOOLEAN NOT NULL DEFAULT FALSE,
                        subscribe_test BOOLEAN NOT NULL DEFAULT FALSE,
                        has_new_update BOOLEAN NOT NULL DEFAULT FALSE,
                        UNIQUE KEY unique_subscription (user_id, thread_id),
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (thread_id) REFERENCES managed_threads(thread_id) ON DELETE CASCADE
                    );
                """)
                # 表4: 作者关注表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS author_follows (
                        follow_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        follower_id BIGINT UNSIGNED NOT NULL,
                        author_id BIGINT UNSIGNED NOT NULL,
                        UNIQUE KEY unique_follow (follower_id, author_id),
                        FOREIGN KEY (follower_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (author_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                """)
                # 表5: 作者动态通知表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS follower_thread_notifications (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        follower_id BIGINT UNSIGNED NOT NULL,
                        thread_id BIGINT UNSIGNED NOT NULL,
                        guild_id BIGINT UNSIGNED NOT NULL,
                        UNIQUE KEY unique_notification (follower_id, thread_id),
                        FOREIGN KEY (follower_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (thread_id) REFERENCES managed_threads(thread_id) ON DELETE CASCADE
                    );
                """)

                # 兼容旧表，检查 guild_id 列
                await cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'managed_threads' AND COLUMN_NAME = 'guild_id'
                """)
                if (await cursor.fetchone())[0] == 0:
                    await cursor.execute("ALTER TABLE managed_threads ADD COLUMN guild_id BIGINT UNSIGNED NULL;")

                await cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'thread_subscriptions' AND COLUMN_NAME = 'guild_id'
                """)
                if (await cursor.fetchone())[0] == 0:
                    await cursor.execute("ALTER TABLE thread_subscriptions ADD COLUMN guild_id BIGINT UNSIGNED NULL;")

                await cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'follower_thread_notifications' AND COLUMN_NAME = 'guild_id'
                """)
                if (await cursor.fetchone())[0] == 0:
                    await cursor.execute("ALTER TABLE follower_thread_notifications ADD COLUMN guild_id BIGINT UNSIGNED NULL;")

                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        migration_key VARCHAR(100) NOT NULL PRIMARY KEY,
                        applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                await cursor.execute("SELECT 1 FROM schema_migrations WHERE migration_key = %s", (GUILD_ID_MIGRATION_KEY,))
                migration_done = await cursor.fetchone()
                if not migration_done:
                    # 使用 thread_id (Discord Snowflake) 作为时间序，选取最新帖子作为迁移锚点
                    await cursor.execute("SELECT thread_id, guild_id FROM managed_threads ORDER BY thread_id DESC LIMIT 1")
                    latest_thread = await cursor.fetchone()
                    if latest_thread:
                        latest_thread_id, latest_guild_id = latest_thread
                        if not latest_guild_id:
                            await cursor.execute(
                                "UPDATE managed_threads SET guild_id = %s WHERE guild_id IS NULL AND thread_id <= %s",
                                (DEFAULT_GUILD_ID, latest_thread_id)
                            )

                    await cursor.execute("""
                        UPDATE thread_subscriptions ts
                        JOIN managed_threads mt ON ts.thread_id = mt.thread_id
                        SET ts.guild_id = mt.guild_id
                        WHERE ts.guild_id IS NULL AND mt.guild_id IS NOT NULL
                    """)

                    await cursor.execute("""
                        UPDATE follower_thread_notifications ftn
                        JOIN managed_threads mt ON ftn.thread_id = mt.thread_id
                        SET ftn.guild_id = mt.guild_id
                        WHERE ftn.guild_id IS NULL AND mt.guild_id IS NOT NULL
                    """)

                    await cursor.execute(
                        "INSERT IGNORE INTO schema_migrations (migration_key) VALUES (%s)",
                        (GUILD_ID_MIGRATION_KEY,)
                    )

                #表6：帖子单独的权限组
                await cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'managed_threads' 
                      AND COLUMN_NAME = 'thread_permission_group_1'
                """)
                if (await cursor.fetchone())[0] == 0:
                    logging.debug("为 managed_threads 表添加 thread_permission_group 列...")
                    await cursor.execute("""
                        ALTER TABLE managed_threads
                        ADD COLUMN thread_permission_group_1 BIGINT UNSIGNED DEFAULT NULL,
                        ADD COLUMN thread_permission_group_2 BIGINT UNSIGNED DEFAULT NULL,
                        ADD COLUMN thread_permission_group_3 BIGINT UNSIGNED DEFAULT NULL,
                        ADD COLUMN thread_permission_group_4 BIGINT UNSIGNED DEFAULT NULL;
                    """)
                    logging.debug("权限组列已成功添加。")
                await conn.commit()
        logging.info("数据库表结构已确认。")
    except Exception as err:
        logging.critical(f"数据库建表失败: {err}")
        raise

async def check_and_create_user(db_pool: aiomysql.pool.Pool, user_id: int):
    """检查用户是否存在，如果不存在则在数据库中创建"""
    if not user_id:
        return
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id", (user_id,))
            await conn.commit()
    except Exception as err:
        logging.critical(f"数据库错误于 check_and_create_user: {err}")