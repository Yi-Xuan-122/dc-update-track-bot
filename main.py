import os 
from dotenv import load_dotenv
from src.bot_app import MyBot
import logging
import logging.handlers
import sys

# --- LOGGING CONFIG
logger = logging.getLogger()
log_format = logging.Formatter(
    '[%(asctime)s] [%(levelname)-8s] [%(name)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)
file_handler = logging.handlers.RotatingFileHandler(
    filename='/tmp/bot.log', 
    encoding='utf-8', 
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # 保留5个备份
    mode='a'
)
file_handler.setFormatter(log_format)
logger.addHandler(console_handler)
logger.addHandler(file_handler)
# --- END ---
load_dotenv()

def main():
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging,log_level_str,None)
    if not isinstance(log_level, int):
        log_level = logging.INFO
        logger.warning(f"无效的日志级别 '{log_level_str}'。将使用默认级别 'INFO'。")
    logger.setLevel(log_level)
    logger.info(f"日志级别已设置为 {log_level_str}")
    
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        logging.error("Error: DISCORD_TOKEN is not set in the .env file.")
        return
    
    bot = MyBot()
    logging.info("机器人正在启动...")
    bot.run(DISCORD_TOKEN)
if __name__ == "__main__":
    main()