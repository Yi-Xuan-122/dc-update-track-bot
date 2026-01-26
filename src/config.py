import os
from dotenv import load_dotenv
import datetime
import logging
log = logging.getLogger(__name__)
# 加载 .env 文件
load_dotenv()

# --- Bot 配置 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_GUILD_ID = int(os.getenv("TARGET_GUILD_ID"))
ADMIN_IDS = [int(uid.strip()) for uid in os.getenv("ADMIN_IDS", "").split(',') if uid.strip().isdigit()]
ALLOWED_CHANNELS = [int(c.strip()) for c in os.getenv("ALLOWED_CHANNELS").split(",")]

# --- Embed 文本配置 ---
EMBED_TITLE = os.getenv("EMBED_TITLE")
EMBED_TEXT = os.getenv("EMBED_TEXT")
EMBED_ERROR = os.getenv("EMBED_ERROR")
UPDATE_TITLE = os.getenv("UPDATE_TITLE")
UPDATE_TEXT = os.getenv("UPDATE_TEXT")
UPDATE_ERROR = os.getenv("UPDATE_ERROR")
DM_PANEL_TITLE = os.getenv("DM_PANEL_TITLE")
DM_PANEL_TEXT = os.getenv("DM_PANEL_TEXT")
VIEW_UPDATES_TITLE = os.getenv("VIEW_UPDATES_TITLE")
VIEW_UPDATES_TEXT = os.getenv("VIEW_UPDATES_TEXT")
MANAGE_SUBS_TITLE = os.getenv("MANAGE_SUBS_TITLE")
MANAGE_AUTHORS_TITLE = os.getenv("MANAGE_AUTHORS_TITLE")
TRACK_NEW_THREAD_EMBED_TITLE = os.getenv("TRACK_NEW_THREAD_EMBED_TITLE")
TRACK_NEW_THREAD_EMBED_TEXT = os.getenv("TRACK_NEW_THREAD_EMBED_TEXT")

# --- 功能参数 ---
UPDATE_MENTION_MAX_NUMBER = int(os.getenv("UPDATE_MENTION_MAX_NUMBER", 50))
UPDATE_MENTION_DELAY = int(os.getenv("UPDATE_MENTION_DELAY", 1000))
UPDATES_PER_PAGE = int(os.getenv("UPDATES_PER_PAGE", 5))
TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS = int(os.getenv("TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS", 1))

# --- 数据库配置 ---
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
POOL_SIZE = int(os.getenv("POOL_SIZE", 10))

# --- LLM config ---
SUMMARY_SETUP = int(os.getenv("SUMMARY_SETUP",0))
BASE_URL = os.getenv('BASE_URL')
MODEL = os.getenv('MODEL')
LLM_FORMAT = os.getenv("LLM_FORMAT", "openai")
if int(os.getenv('IMG_VIEW')) == 1:
    IMG_VIEW = True
else:
    IMG_VIEW = False
ADDITIONAL_HEADER = os.getenv('ADDITIONAL_HEADER')
BODY_ARGUMENT = os.getenv('BODY_ARGUMENT')
LLM_ALLOW_CHANNELS = [int(c.strip()) for c in os.getenv("LLM_ALLOW_CHANNELS").split(",")]
LLM_CHAT_SETUP = int(os.getenv("LLM_CHAT_SETUP",0))
if int(os.getenv('GEMINI_SEARCH', 0)) == 1:
    GEMINI_SEARCH = True
else:
    GEMINI_SEARCH = False
GEMINI_TOOL_CALL = os.getenv("GEMINI_TOOL_CALL","gemini")


# --- summary config ---
utc_zone_str = os.getenv('UTC_ZONE')
if utc_zone_str is None:
    logging.warning("UTC_ZONE not set or invaild. UTC_ZONE will be set 0")
    UTC_ZONE = 0
else:
    try:
        UTC_ZONE = int(utc_zone_str)
        if UTC_ZONE<-12 or UTC_ZONE>12:
            logging.warning("UTC_ZONE invaild. UTC_ZONE will be set 0")
            UTC_ZONE = 0
    except ValueError as e:
        logging.warning("UTC_ZONE invaild. UTC_ZONE will be set 0")

UTC_PLUS_8 = datetime.timezone(datetime.timedelta(hours=8))
def get_utc8_now_str():
    return datetime.datetime.now(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")

print(f"DEBUG: BASE_URL is {os.getenv('BASE_URL')}")