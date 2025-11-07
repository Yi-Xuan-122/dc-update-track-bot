import aiomysql
import asyncio
from src import config
import logging
log = logging.getLogger(__name__)

async def create_db_pool():
    """创建并返回一个aiomysql数据库连接池，包含重试逻辑"""
    max_retries = 10
    retry_delay = 5
    for i in range(max_retries):
        try:
            pool = await aiomysql.create_pool(
                host='db',
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                db=config.MYSQL_DATABASE,
                port=3306,
                minsize=1,
                maxsize=config.POOL_SIZE,
                pool_recycle=600,
                autocommit=False
            )
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
            logging.info("数据库连接池创建成功。")
            return pool
        except Exception as err:
            logging.info(f"数据库连接失败 (尝试 {i+1}/{max_retries}): {err}")
            if i + 1 == max_retries:
                logging.critical("已达到最大重试次数，无法连接到数据库。")
                return None
            logging.info(f"将在 {retry_delay} 秒后重试...")
            await asyncio.sleep(retry_delay)
    return None
