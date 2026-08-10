# config.py
import os
import sys
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, List, Dict, Optional
from dotenv import load_dotenv

logger = logging.getLogger("Config")
load_dotenv(override=True)

# =====================================================================
# SECTION 1: BASE PATHS & SYSTEM DIRECTORIES
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

is_termux = "com.termux" in sys.executable or "/data/data/com.termux" in str(BASE_DIR)
is_emulated = "emulated" in str(BASE_DIR)

if is_termux or is_emulated:
    SAFE_DB_DIR = Path.home() / ".baziliksina"
    SAFE_DB_DIR.mkdir(parents=True, exist_ok=True)
else:
    SAFE_DB_DIR = BASE_DIR

WORKSPACE_DIR = BASE_DIR / "bot_workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

CONFIG_JSON_PATH = BASE_DIR / "config" / "config.json"
CHARACTER_FILE = os.getenv("CHARACTER_FILE", "character.txt")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# =====================================================================
# HELPER FUNCTIONS FOR TYPE CASTING & ENV PARSING
# =====================================================================
def _parse_list(key: str, default: list = None) -> list:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default if default is not None else []
    return [p.strip() for p in raw.split(",") if p.strip()]

def _parse_int_list(key: str, default: list = None) -> list:
    raw = _parse_list(key, default)
    res = []
    for item in raw:
        try:
            res.append(int(item))
        except ValueError:
            res.append(item)
    return res

def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is not None:
        return val.strip().lower() in ["true", "1", "yes"]
    return default

def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is not None:
        try: return int(val.strip())
        except ValueError: pass
    return default

def _get_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is not None:
        try: return float(val.strip())
        except ValueError: pass
    return default


# =====================================================================
# SECTION 2: TELEGRAM CORE, SESSIONS & ADMIN RANKS
# =====================================================================
TELEGRAM_API_ID = _get_int("TELEGRAM_API_ID", 0)
API_ID = TELEGRAM_API_ID

TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
API_HASH = TELEGRAM_API_HASH

TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "baziliksina_session")
SESSION_NAME = TELEGRAM_SESSION_NAME
SESSION_PATH = str(SAFE_DB_DIR / SESSION_NAME)

OWNER_ID = _get_int("OWNER_ID", 2113692455)

_raw_admins = os.getenv("ADMINS", "")
ADMINS = {}
if _raw_admins:
    try:
        ADMINS = json.loads(_raw_admins)
    except Exception:
        ADMINS = {OWNER_ID: {"rank": 100, "permissions": ["all"]}}
else:
    ADMINS = {OWNER_ID: {"rank": 100, "permissions": ["all"]}}

TELEGRAM_CONNECT_TIMEOUT = _get_float("TELEGRAM_CONNECT_TIMEOUT", 15.0)
TELEGRAM_CONNECTION_RETRIES = _get_int("TELEGRAM_CONNECTION_RETRIES", 5)
TELEGRAM_RETRY_DELAY = _get_float("TELEGRAM_RETRY_DELAY", 5.0)
TELEGRAM_AUTO_RECONNECT = _get_bool("TELEGRAM_AUTO_RECONNECT", True)
TELEGRAM_TIMEOUT = _get_float("TELEGRAM_TIMEOUT", 15.0)

TELEGRAM_METHOD_BLACKLIST = {
    "log_out", "delete_account", "disconnect", "sign_in", "send_code_request", "switch_account"
}


# =====================================================================
# SECTION 3: DATABASE CONFIGURATION
# =====================================================================
DB_NAME = os.getenv("DB_NAME", "bot_context.db")
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL")
BOOTSTRAP_DATABASE = _get_bool("BOOTSTRAP_DATABASE", False)


# =====================================================================
# SECTION 4: GOOGLE GEMINI AI CONFIGURATION
# =====================================================================
_gemini_keys_raw = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_raw.split(",") if k.strip()]
GEMINI_KEYS = GEMINI_API_KEYS

_gemini_models_raw = os.getenv("GEMINI_MODELS", "") or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_MODELS = [m.strip() for m in _gemini_models_raw.split(",") if m.strip()]

THINKING_LEVEL = os.getenv("THINKING_LEVEL", "high").lower()
TEMPERATURE = _get_float("TEMPERATURE", 0.7)
TOP_P = _get_float("TOP_P", 0.95)
STOP_SEQUENCES = _parse_list("STOP_SEQUENCES", [])
OUTPUT_LENGTH = _get_int("OUTPUT_LENGTH", 65536)
INPUT_TOKEN_LIMIT = _get_int("INPUT_TOKEN_LIMIT", 524288)

SAFETY_HATE_SPEECH = os.getenv("SAFETY_HATE_SPEECH", "BLOCK_NONE")
SAFETY_HARASSMENT = os.getenv("SAFETY_HARASSMENT", "BLOCK_NONE")
SAFETY_SEXUALLY_EXPLICIT = os.getenv("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE")
SAFETY_DANGEROUS_CONTENT = os.getenv("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE")


# =====================================================================
# SECTION 5: POLLINATIONS AI GENERATIVE MEDIA
# =====================================================================
_pollinations_keys_raw = os.getenv("POLLINATIONS_KEYS", "")
POLLINATIONS_KEYS = [k.strip() for k in _pollinations_keys_raw.split(",") if k.strip()]

DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
DEFAULT_IMAGE_WIDTH = _get_int("DEFAULT_IMAGE_WIDTH", 1024)
DEFAULT_IMAGE_HEIGHT = _get_int("DEFAULT_IMAGE_HEIGHT", 1024)
MEDIA_RESOLUTION = os.getenv("MEDIA_RESOLUTION", "high").lower()
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "auto").lower()
GENERATE_IMAGE_TIMEOUT = _get_float("GENERATE_IMAGE_TIMEOUT", 180.0)

DEFAULT_AUDIO_VOICE = os.getenv("DEFAULT_AUDIO_VOICE", "nova")
DEFAULT_AUDIO_MODEL = os.getenv("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct")
GENERATE_AUDIO_TIMEOUT = _get_float("GENERATE_AUDIO_TIMEOUT", 120.0)

DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "wan")
DEFAULT_VIDEO_DURATION = _get_int("DEFAULT_VIDEO_DURATION", 5)
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("DEFAULT_VIDEO_ASPECT_RATIO", "1:1")
GENERATE_VIDEO_TIMEOUT = _get_float("GENERATE_VIDEO_TIMEOUT", 180.0)

POLLINATIONS_SEED_MIN = _get_int("POLLINATIONS_SEED_MIN", 1)
POLLINATIONS_SEED_MAX = _get_int("POLLINATIONS_SEED_MAX", 999999999)
POLLINATIONS_UPLOAD_JPEG_QUALITY = _get_int("POLLINATIONS_UPLOAD_JPEG_QUALITY", 95)
DEFAULT_PUBLIC_UPLOAD_PROVIDER = os.getenv("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto")

IMAGE_GEN_AUTO_DOWNLOAD = _get_bool("IMAGE_GEN_AUTO_DOWNLOAD", True)
IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE = _get_bool("IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE", True)
AUDIO_GEN_AUTO_DOWNLOAD = _get_bool("AUDIO_GEN_AUTO_DOWNLOAD", True)
AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE = _get_bool("AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE", True)
VIDEO_GEN_AUTO_DOWNLOAD = _get_bool("VIDEO_GEN_AUTO_DOWNLOAD", True)
VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE = _get_bool("VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE", True)
PUBLIC_UPLOAD_TIMEOUT = _get_float("PUBLIC_UPLOAD_TIMEOUT", 60.0)


# =====================================================================
# SECTION 6: CONTEXT, TOKENS & FILE STRATEGIES
# =====================================================================
CONTEXT_MANAGEMENT_MODE = os.getenv("CONTEXT_MANAGEMENT_MODE", "summarize").lower()
TEXT_LIMIT_TYPE = os.getenv("TEXT_LIMIT_TYPE", "tokens").lower()
TEXT_TOKEN_LIMIT = _get_int("TEXT_TOKEN_LIMIT", 524288)
CONTEXT_TRIM_COUNT = _get_int("CONTEXT_TRIM_COUNT", 20)
MESSAGES_LIMIT = _get_int("MESSAGES_LIMIT", 150)
CONTEXT_LOCAL_RATIO = _get_float("CONTEXT_LOCAL_RATIO", 0.7)
CONTEXT_LOCAL_MIN_LIMIT = _get_int("CONTEXT_LOCAL_MIN_LIMIT", 15)
SUMMARIZATION_MESSAGES_LIMIT = _get_int("SUMMARIZATION_MESSAGES_LIMIT", 500)
SUMMARIZATION_KEEP_LIMIT = _get_int("SUMMARIZATION_KEEP_LIMIT", 15)
CROSS_CHAT_CONTEXT = _get_bool("CROSS_CHAT_CONTEXT", True)

FILE_CONTEXT_MODE = os.getenv("FILE_CONTEXT_MODE", "trim").lower()
FILE_LIMIT_TYPE = os.getenv("FILE_LIMIT_TYPE", "tokens").lower()
FILE_TOKEN_LIMIT = _get_int("FILE_TOKEN_LIMIT", 200000)
FILE_TRIM_COUNT = _get_int("FILE_TRIM_COUNT", 5)
MEDIA_LIMIT = _get_int("MEDIA_LIMIT", 250)
AUTO_ATTACH_FILES_TO_CONTEXT = _get_bool("AUTO_ATTACH_FILES_TO_CONTEXT", False)

AUTO_SAVE_TEXT_RULE = os.getenv("AUTO_SAVE_TEXT_RULE", "all")
AUTO_SAVE_FILE_RULE = os.getenv("AUTO_SAVE_FILE_RULE", "all")


# =====================================================================
# SECTION 7: TRIGGERS, RULES & FLOW MATRIX
# =====================================================================
AI_RESPONSE_MODE = os.getenv("AI_RESPONSE_MODE", "all").strip().lower()
AI_RESPONSE_TRIGGERS = _parse_list("AI_RESPONSE_TRIGGERS", ["name", "username", "mentioned", "reply_to_me"])

SAVE_INCOMING_MESSAGES = _get_bool("SAVE_INCOMING_MESSAGES", True)
SAVE_EDITED_MESSAGES = _get_bool("SAVE_EDITED_MESSAGES", True)
SAVE_DELETED_MESSAGES = _get_bool("SAVE_DELETED_MESSAGES", True)
SAVE_OUTGOING_NEW_MESSAGES = _get_bool("SAVE_OUTGOING_NEW_MESSAGES", True)
SAVE_OUTGOING_EDITED_MESSAGES = _get_bool("SAVE_OUTGOING_EDITED_MESSAGES", True)
SAVE_OUTGOING_DELETED_MESSAGES = _get_bool("SAVE_OUTGOING_DELETED_MESSAGES", True)

TRIGGER_ON_INCOMING = _get_bool("TRIGGER_ON_INCOMING", True)
TRIGGER_ON_EDITED = _get_bool("TRIGGER_ON_EDITED", False)
TRIGGER_ON_DELETED = _get_bool("TRIGGER_ON_DELETED", False)
TRIGGER_ON_OUTGOING_NEW_MESSAGES = _get_bool("TRIGGER_ON_OUTGOING_NEW_MESSAGES", False)
TRIGGER_ON_OUTGOING_EDITED_MESSAGES = _get_bool("TRIGGER_ON_OUTGOING_EDITED_MESSAGES", False)
TRIGGER_ON_OUTGOING_DELETED_MESSAGES = _get_bool("TRIGGER_ON_OUTGOING_DELETED_MESSAGES", False)
TRIGGER_ON_OUTGOING_MANUAL_MESSAGES = _get_bool("TRIGGER_ON_OUTGOING_MANUAL_MESSAGES", False)
TRIGGER_ON_COMMANDS = _get_bool("TRIGGER_ON_COMMANDS", False)

BOOTSTRAP_TRIGGER_GENERATION = _get_bool("BOOTSTRAP_TRIGGER_GENERATION", True)
CATCH_UP_TRIGGER_GENERATION = _get_bool("CATCH_UP_TRIGGER_GENERATION", True)
USE_SYSTEM_PROMPT = _get_bool("USE_SYSTEM_PROMPT", True)


# =====================================================================
# SECTION 8: ADVANCED FILTERS, WHITELISTS & BLACKLISTS
# =====================================================================
FILTER_POLICY = os.getenv("FILTER_POLICY", "blacklist_first").strip().lower()
MSG_SAVE_WHITELIST = _parse_list("MSG_SAVE_WHITELIST", [])
MSG_SAVE_BLACKLIST = _parse_list("MSG_SAVE_BLACKLIST", [])
MSG_GEN_WHITELIST = _parse_list("MSG_GEN_WHITELIST", [])
MSG_GEN_BLACKLIST = _parse_list("MSG_GEN_BLACKLIST", [])
ALLOWED_MESSAGE_TYPES = _parse_list("ALLOWED_MESSAGE_TYPES", ["text", "voice", "video", "photo", "document", "gif", "sticker", "location", "contact", "poll", "venue", "album", "list"])

SAVE_INCOMING_REACTION_ADD = _get_bool("SAVE_INCOMING_REACTION_ADD", True)
SAVE_INCOMING_REACTION_REMOVE = _get_bool("SAVE_INCOMING_REACTION_REMOVE", True)
SAVE_OUTGOING_REACTION_ADD = _get_bool("SAVE_OUTGOING_REACTION_ADD", True)
SAVE_OUTGOING_REACTION_REMOVE = _get_bool("SAVE_OUTGOING_REACTION_REMOVE", True)

TRIGGER_ON_INCOMING_REACTION_ADD = _get_bool("TRIGGER_ON_INCOMING_REACTION_ADD", False)
TRIGGER_ON_INCOMING_REACTION_REMOVE = _get_bool("TRIGGER_ON_INCOMING_REACTION_REMOVE", False)
TRIGGER_ON_OUTGOING_REACTION_ADD = _get_bool("TRIGGER_ON_OUTGOING_REACTION_ADD", False)
TRIGGER_ON_OUTGOING_REACTION_REMOVE = _get_bool("TRIGGER_ON_OUTGOING_REACTION_REMOVE", False)

REACTION_WHITELIST = _parse_list("REACTION_WHITELIST", [])
REACTION_BLACKLIST = _parse_list("REACTION_BLACKLIST", [])

SAVE_USER_METADATA = _get_bool("SAVE_USER_METADATA", True)
SAVE_CHAT_METADATA = _get_bool("SAVE_CHAT_METADATA", True)
USER_CACHE_WHITELIST = _parse_int_list("USER_CACHE_WHITELIST", [])
USER_CACHE_BLACKLIST = _parse_int_list("USER_CACHE_BLACKLIST", [])
CHAT_CACHE_WHITELIST = _parse_int_list("CHAT_CACHE_WHITELIST", [])
CHAT_CACHE_BLACKLIST = _parse_int_list("CHAT_CACHE_BLACKLIST", [])

CHAT_WHITELIST = _parse_int_list("CHAT_WHITELIST", [])
CHAT_BLACKLIST = _parse_int_list("CHAT_BLACKLIST", [])
READ_ACK_WHITELIST = _parse_list("READ_ACK_WHITELIST", ["all"])
READ_ACK_BLACKLIST = _parse_list("READ_ACK_BLACKLIST", [])

AI_OUTPUT_WHITELIST_REGEX = _parse_list("AI_OUTPUT_WHITELIST_REGEX", [])
AI_OUTPUT_BLACKLIST_REGEX = _parse_list("AI_OUTPUT_BLACKLIST_REGEX", [])

AI_ALLOWED_ROOT_TOOLS = _parse_list("AI_ALLOWED_ROOT_TOOLS", ["all"])
AI_BLOCKED_ROOT_TOOLS = _parse_list("AI_BLOCKED_ROOT_TOOLS", ["execute_python_code", "run_sandboxed_command"])
AI_ALLOWED_CUSTOM_TOOLS = _parse_list("AI_ALLOWED_CUSTOM_TOOLS", ["all"])
AI_BLOCKED_CUSTOM_TOOLS = _parse_list("AI_BLOCKED_CUSTOM_TOOLS", [])
AI_ALLOWED_MIMES = _parse_list("AI_ALLOWED_MIMES", ["all"])
AI_BLOCKED_MIMES = _parse_list("AI_BLOCKED_MIMES", ["none"])


# =====================================================================
# SECTION 9: AI PIPELINE & GRANULAR PERMISSIONS MATRIX
# =====================================================================
AI_ALLOW_PIPELINES = _get_bool("AI_ALLOW_PIPELINES", True)
AI_ALLOWED_PIPELINE_OPERATORS = os.getenv("AI_ALLOWED_PIPELINE_OPERATORS", ";,&&,||,|")
AI_BLOCKED_PIPELINE_OPERATORS = os.getenv("AI_BLOCKED_PIPELINE_OPERATORS", "")

AI_PERM_COMMANDS_CREATE = _get_bool("AI_PERM_COMMANDS_CREATE", True)
AI_PERM_COMMANDS_EDIT = _get_bool("AI_PERM_COMMANDS_EDIT", True)
AI_PERM_COMMANDS_DELETE = _get_bool("AI_PERM_COMMANDS_DELETE", True)
AI_PERM_COMMANDS_VIEW_INFO = _get_bool("AI_PERM_COMMANDS_VIEW_INFO", True)
AI_PERM_COMMANDS_VIEW_CONTENT = _get_bool("AI_PERM_COMMANDS_VIEW_CONTENT", True)
AI_PERM_COMMANDS_LIST = _get_bool("AI_PERM_COMMANDS_LIST", True)
AI_PERM_COMMANDS_INVOKE = _get_bool("AI_PERM_COMMANDS_INVOKE", True)

AI_PERM_TOOLS_CREATE = _get_bool("AI_PERM_TOOLS_CREATE", True)
AI_PERM_TOOLS_EDIT = _get_bool("AI_PERM_TOOLS_EDIT", True)
AI_PERM_TOOLS_DELETE = _get_bool("AI_PERM_TOOLS_DELETE", True)
AI_PERM_TOOLS_VIEW_INFO = _get_bool("AI_PERM_TOOLS_VIEW_INFO", True)
AI_PERM_TOOLS_VIEW_CONTENT = _get_bool("AI_PERM_TOOLS_VIEW_CONTENT", True)
AI_PERM_TOOLS_LIST = _get_bool("AI_PERM_TOOLS_LIST", True)
AI_PERM_TOOLS_INVOKE = _get_bool("AI_PERM_TOOLS_INVOKE", True)

AI_PERM_TAGS_CREATE = _get_bool("AI_PERM_TAGS_CREATE", True)
AI_PERM_TAGS_EDIT = _get_bool("AI_PERM_TAGS_EDIT", True)
AI_PERM_TAGS_DELETE = _get_bool("AI_PERM_TAGS_DELETE", True)
AI_PERM_TAGS_VIEW_INFO = _get_bool("AI_PERM_TAGS_VIEW_INFO", True)
AI_PERM_TAGS_VIEW_CONTENT = _get_bool("AI_PERM_TAGS_VIEW_CONTENT", True)
AI_PERM_TAGS_LIST = _get_bool("AI_PERM_TAGS_LIST", True)
AI_PERM_TAGS_INVOKE = _get_bool("AI_PERM_TAGS_INVOKE", True)

AI_PERM_SERVICES_CREATE = _get_bool("AI_PERM_SERVICES_CREATE", True)
AI_PERM_SERVICES_EDIT = _get_bool("AI_PERM_SERVICES_EDIT", True)
AI_PERM_SERVICES_DELETE = _get_bool("AI_PERM_SERVICES_DELETE", True)
AI_PERM_SERVICES_VIEW_INFO = _get_bool("AI_PERM_SERVICES_VIEW_INFO", True)
AI_PERM_SERVICES_VIEW_CONTENT = _get_bool("AI_PERM_SERVICES_VIEW_CONTENT", True)
AI_PERM_SERVICES_LIST = _get_bool("AI_PERM_SERVICES_LIST", True)
AI_PERM_SERVICES_INVOKE = _get_bool("AI_PERM_SERVICES_INVOKE", True)

AI_PERM_CRON_CREATE = _get_bool("AI_PERM_CRON_CREATE", True)
AI_PERM_CRON_EDIT = _get_bool("AI_PERM_CRON_EDIT", True)
AI_PERM_CRON_DELETE = _get_bool("AI_PERM_CRON_DELETE", True)
AI_PERM_CRON_VIEW_INFO = _get_bool("AI_PERM_CRON_VIEW_INFO", True)
AI_PERM_CRON_VIEW_CONTENT = _get_bool("AI_PERM_CRON_VIEW_CONTENT", True)
AI_PERM_CRON_LIST = _get_bool("AI_PERM_CRON_LIST", True)
AI_PERM_CRON_INVOKE = _get_bool("AI_PERM_CRON_INVOKE", True)

AI_PERM_SITES_CREATE = _get_bool("AI_PERM_SITES_CREATE", True)
AI_PERM_SITES_EDIT = _get_bool("AI_PERM_SITES_EDIT", True)
AI_PERM_SITES_DELETE = _get_bool("AI_PERM_SITES_DELETE", True)
AI_PERM_SITES_VIEW_INFO = _get_bool("AI_PERM_SITES_VIEW_INFO", True)
AI_PERM_SITES_VIEW_CONTENT = _get_bool("AI_PERM_SITES_VIEW_CONTENT", True)
AI_PERM_SITES_LIST = _get_bool("AI_PERM_SITES_LIST", True)
AI_PERM_SITES_INVOKE = _get_bool("AI_PERM_SITES_INVOKE", True)


# =====================================================================
# SECTION 10: TIMEOUTS, INTERVALS & LIMITS
# =====================================================================
MAX_TURNS = _get_int("MAX_TURNS", 1000)
DEBOUNCE_DELAY = _get_float("DEBOUNCE_DELAY", 7.0)
TIMERS_LOOP_INTERVAL = _get_float("TIMERS_LOOP_INTERVAL", 1.0)
KEEP_ALIVE_INTERVAL = _get_int("KEEP_ALIVE_INTERVAL", 120)
CONNECTION_MONITOR_INTERVAL = _get_int("CONNECTION_MONITOR_INTERVAL", 10)
GEMINI_TIMEOUT = _get_float("GEMINI_TIMEOUT", 90.0)
TYPING_INTERVAL = _get_float("TYPING_INTERVAL", 10.0)
TIMEOUT_SLEEP = _get_float("TIMEOUT_SLEEP", 2.0)
QUEUE_PROMOTION_DELAY = _get_float("QUEUE_PROMOTION_DELAY", 2.0)
RATE_LIMIT_SLEEP = _get_float("RATE_LIMIT_SLEEP", 5.0)
API_ERROR_SLEEP = _get_float("API_ERROR_SLEEP", 2.0)
PROFILE_UPDATE_INTERVAL = _get_int("PROFILE_UPDATE_INTERVAL", 3600)
TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT = _get_int("TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT", 10)
BOT_RESPONSE_TIMEOUT = _get_float("BOT_RESPONSE_TIMEOUT", 6.0)
BUTTON_CLICK_TIMEOUT = _get_float("BUTTON_CLICK_TIMEOUT", 15.0)
DOWNLOAD_MEDIA_TIMEOUT = _get_float("DOWNLOAD_MEDIA_TIMEOUT", 120.0)
TELEGRAM_ACTION_TIMEOUT = _get_float("TELEGRAM_ACTION_TIMEOUT", 60.0)
CONVERSION_TIMEOUT = _get_float("CONVERSION_TIMEOUT", 30.0)
GOOGLE_UPLOAD_TIMEOUT = _get_float("GOOGLE_UPLOAD_TIMEOUT", 120.0)
KEY_INFO_TIMEOUT = _get_float("KEY_INFO_TIMEOUT", 10.0)

GEMINI_FREE_RECOVERY_TIME = _get_int("GEMINI_FREE_RECOVERY_TIME", 18000)
GEMINI_PRO_RECOVERY_TIME = _get_int("GEMINI_PRO_RECOVERY_TIME", 86400)
GEMINI_DEAD_KEY_COOLDOWN = _get_int("GEMINI_DEAD_KEY_COOLDOWN", 31536000)
POLLINATIONS_KEY_RECOVERY_TIME = _get_int("POLLINATIONS_KEY_RECOVERY_TIME", 3600)

MAX_FILE_SIZE = _get_int("MAX_FILE_SIZE", 15 * 1024 * 1024)
DUPLICATE_CACHE_SIZE = _get_int("DUPLICATE_CACHE_SIZE", 1000)
AVATAR_CACHE_TIME = _get_int("AVATAR_CACHE_TIME", 86400)
DEFAULT_RESULT_INDEX = _get_int("DEFAULT_RESULT_INDEX", 0)

DIALOGS_LIMIT = _get_int("DIALOGS_LIMIT", 50)
BOOTSTRAP_MESSAGES_LIMIT = _get_int("BOOTSTRAP_MESSAGES_LIMIT", 20)
MISSED_MESSAGES_LIMIT = _get_int("MISSED_MESSAGES_LIMIT", 50)


# =====================================================================
# SECTION 11: PROXY & TOR CONFIGURATION
# =====================================================================
TELEGRAM_PROXIES = _parse_list("TELEGRAM_PROXIES", [])
GEMINI_PROXIES = _parse_list("GEMINI_PROXIES", [])
POLLINATIONS_PROXIES = _parse_list("POLLINATIONS_PROXIES", [])
SCRAPER_PROXIES = _parse_list("SCRAPER_PROXIES", [])

_raw_all_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or ""
ALL_PROXY = _raw_all_proxy

TOR_HOST = os.getenv("TOR_HOST", "127.0.0.1")
TOR_SOCKS_PORT = _get_int("TOR_SOCKS_PORT", 9050)
TOR_CONTROL_PORT = _get_int("TOR_CONTROL_PORT", 9051)
TOR_PASSWORD = os.getenv("TOR_PASSWORD", "")
TOR_ROTATION_TIMEOUT = _get_float("TOR_ROTATION_TIMEOUT", 15.0)

POLLINATIONS_MAX_ATTEMPTS = _get_int("POLLINATIONS_MAX_ATTEMPTS", 8)
TOR_MAX_CONSECUTIVE_FAILURES = _get_int("TOR_MAX_CONSECUTIVE_FAILURES", 2)
PROXY_CHECK_TIMEOUT = _get_float("PROXY_CHECK_TIMEOUT", 3.0)
PROXY_STRICT_CHECK = _get_bool("PROXY_STRICT_CHECK", False)

if GEMINI_PROXIES:
    os.environ["HTTP_PROXY"] = GEMINI_PROXIES[0]
    os.environ["HTTPS_PROXY"] = GEMINI_PROXIES[0]
    os.environ["ALL_PROXY"] = GEMINI_PROXIES[0]


# =====================================================================
# SECTION 12: SECURITY BLACKLISTS & REGEXES
# =====================================================================
SQL_SELECT_LIMIT = _get_int("SQL_SELECT_LIMIT", 100)
SQL_STDOUT_CHAR_LIMIT = _get_int("SQL_STDOUT_CHAR_LIMIT", 3500)
TELEGRAM_ACTION_CHAR_LIMIT = _get_int("TELEGRAM_ACTION_CHAR_LIMIT", 5000)
TELEGRAM_ACTION_CONFIRM_LIMIT = _get_int("TELEGRAM_ACTION_CONFIRM_LIMIT", 500)
VM_STDOUT_NOTICE_LIMIT = _get_int("VM_STDOUT_NOTICE_LIMIT", 1500)
SANDBOX_COMMAND_CHAR_LIMIT = _get_int("SANDBOX_COMMAND_CHAR_LIMIT", 3000)

WEB_SEARCH_RESULTS_LIMIT = _get_int("WEB_SEARCH_RESULTS_LIMIT", 50)
WEB_MEDIA_SEARCH_RESULTS_LIMIT = _get_int("WEB_MEDIA_SEARCH_RESULTS_LIMIT", 3)
WEB_MEDIA_SEARCH_CANDIDATES_LIMIT = _get_int("WEB_MEDIA_SEARCH_CANDIDATES_LIMIT", 50)
WEB_DEEP_SEARCH_CANDIDATES_LIMIT = _get_int("WEB_DEEP_SEARCH_CANDIDATES_LIMIT", 3)
WEB_DEEP_SEARCH_CHAR_LIMIT = _get_int("WEB_DEEP_SEARCH_CHAR_LIMIT", 10000)
SCRAPE_CHAR_LIMIT = _get_int("SCRAPE_CHAR_LIMIT", 4000)
WEB_SEARCH_TIMEOUT = _get_float("WEB_SEARCH_TIMEOUT", 10.0)
WEB_MEDIA_SEARCH_TIMEOUT = _get_float("WEB_MEDIA_SEARCH_TIMEOUT", 10.0)
SCRAPE_TIMEOUT = _get_float("SCRAPE_TIMEOUT", 10.0)

SANDBOX_ALLOWED_FILES = os.getenv("SANDBOX_ALLOWED_FILES", "all")
SANDBOX_BLOCKED_FILES = os.getenv("SANDBOX_BLOCKED_FILES", "bot.py,config.py,db_manager.py,key_manager.py,gemini_manager.py,context_manager.py,permission_manager.py,service_manager.py,command_manager.py,prompt_interpolator.py,response_executor.py,sandbox.py,registry.py,utils.py,parser.py,downloader.py,proxy_manager.py,server.py,services.py,main.py,system_tools.py,file_tools.py,web_tools.py,telegram_tools.py,scheduler_tools.py,media_tools.py,site_tools.py,command_tools.py,service_tools.py,tag_block_tools.py,.env,.env.example,bot_context.db,bot_context.db-wal,bot_context.db-shm,baziliksina.session,baziliksina.session-journal,config.json,character.txt,system_prompt.txt,rules_prompt.txt,env_prompt.txt,summarize_prompt.txt,feedback_prompt.txt")

SANDBOX_CONFIG_WHITELIST = _parse_list("SANDBOX_CONFIG_WHITELIST", ["all"])
SANDBOX_CONFIG_BLACKLIST = _parse_list("SANDBOX_CONFIG_BLACKLIST", ["API_HASH", "TELEGRAM_API_HASH", "GEMINI_API_KEYS", "GEMINI_KEYS", "POLLINATIONS_KEYS", "TOR_PASSWORD", "ALL_PROXY", "all_proxy", "TELEGRAM_PROXIES", "GEMINI_PROXIES", "POLLINATIONS_PROXIES", "SCRAPER_PROXIES"])

GAME_EMOJI_WHITELIST = _parse_list("GAME_EMOJI_WHITELIST", ["🎲", "🎯", "🎳", "🏀", "⚽", "🎰"])
GAME_EMOJI_BLACKLIST = _parse_list("GAME_EMOJI_BLACKLIST", [])

SANDBOX_COMMAND_WHITELIST = _parse_list("SANDBOX_COMMAND_WHITELIST", ["all"])
SANDBOX_COMMAND_BLACKLIST = _parse_list("SANDBOX_COMMAND_BLACKLIST", ["rm", "sudo", "reboot", "shutdown", "init", "passwd", "chown", "chmod", "dd", "mkfs", "parted", "fdisk", "mkswap", "killall", "pkill", "kill", "mv", "systemctl", "service"])
SANDBOX_COMMAND_REGEX_BLACKLIST = os.getenv("SANDBOX_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|\.session|\.db|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|context_manager\.py|permission_manager\.py|service_manager\.py|command_manager\.py|prompt_interpolator\.py|response_executor\.py|sandbox\.py|registry\.py|utils\.py|parser\.py|downloader\.py|proxy_manager\.py|server\.py|services\.py|main\.py|tools|core|database|services|server|utils|\.txt|\.json)")
SANDBOX_COMMAND_REGEX_WHITELIST = os.getenv("SANDBOX_COMMAND_REGEX_WHITELIST", "")

BOT_COMMAND_WHITELIST = _parse_list("BOT_COMMAND_WHITELIST", ["all"])
BOT_COMMAND_BLACKLIST = _parse_list("BOT_COMMAND_BLACKLIST", [])

OUTGOING_FILE_WHITELIST = _parse_list("OUTGOING_FILE_WHITELIST", ["all"])
OUTGOING_FILE_BLACKLIST = _parse_list("OUTGOING_FILE_BLACKLIST", [])

TELEGRAM_ACTION_WHITELIST = _parse_list("TELEGRAM_ACTION_WHITELIST", ["all"])
TELEGRAM_ACTION_BLACKLIST = _parse_list("TELEGRAM_ACTION_BLACKLIST", ["log_out", "delete_account", "disconnect", "sign_in", "send_code_request", "switch_account"])

SANDBOX_PYTHON_WHITELIST = _parse_list("SANDBOX_PYTHON_WHITELIST", ["all"])
SANDBOX_PYTHON_BLACKLIST = _parse_list("SANDBOX_PYTHON_BLACKLIST", ["os.system", "os.popen", "subprocess", "shutil.rmtree", "eval", "exec"])

INCOMING_FILE_WHITELIST = _parse_list("INCOMING_FILE_WHITELIST", ["all"])
INCOMING_FILE_BLACKLIST = _parse_list("INCOMING_FILE_BLACKLIST", [])
INLINE_CALLBACK_WHITELIST = _parse_list("INLINE_CALLBACK_WHITELIST", ["all"])
INLINE_CALLBACK_BLACKLIST = _parse_list("INLINE_CALLBACK_BLACKLIST", [])
KEYBOARD_BUTTON_WHITELIST = _parse_list("KEYBOARD_BUTTON_WHITELIST", ["all"])
KEYBOARD_BUTTON_BLACKLIST = _parse_list("KEYBOARD_BUTTON_BLACKLIST", [])
AI_TAG_WHITELIST = _parse_list("AI_TAG_WHITELIST", ["all"])
AI_TAG_BLACKLIST = _parse_list("AI_TAG_BLACKLIST", [])
AI_BLOCK_WHITELIST = _parse_list("AI_BLOCK_WHITELIST", ["all"])
AI_BLOCK_BLACKLIST = _parse_list("AI_BLOCK_BLACKLIST", [])
CUSTOM_TAG_BLOCK_CODE_WHITELIST = _parse_list("CUSTOM_TAG_BLOCK_CODE_WHITELIST", ["all"])
CUSTOM_TAG_BLOCK_CODE_BLACKLIST = _parse_list("CUSTOM_TAG_BLOCK_CODE_BLACKLIST", [])
GROUP_SETTINGS_WHITELIST = _parse_list("GROUP_SETTINGS_WHITELIST", ["all"])
GROUP_SETTINGS_BLACKLIST = _parse_list("GROUP_SETTINGS_BLACKLIST", [])
CONTACTS_MANAGE_WHITELIST = _parse_list("CONTACTS_MANAGE_WHITELIST", ["all"])
CONTACTS_MANAGE_BLACKLIST = _parse_list("CONTACTS_MANAGE_BLACKLIST", [])
ACCOUNT_SETTINGS_WHITELIST = _parse_list("ACCOUNT_SETTINGS_WHITELIST", ["all"])
ACCOUNT_SETTINGS_BLACKLIST = _parse_list("ACCOUNT_SETTINGS_BLACKLIST", [])


# =====================================================================
# SECTION 13: RESTful WEB SERVER PARAMETERS
# =====================================================================
WEB_SERVER_ENABLE = _get_bool("WEB_SERVER_ENABLE", True)
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
WEB_SERVER_PORT = _get_int("WEB_SERVER_PORT", 8080)
WEB_SERVER_SUBDOMAIN = os.getenv("WEB_SERVER_SUBDOMAIN", "").strip()
WEB_SERVER_LOG_PATH = os.getenv("WEB_SERVER_LOG_PATH", "bot.log")
WEB_SERVER_IP_ACL = _parse_list("WEB_SERVER_IP_ACL", [])
WEB_SERVER_IP_DETECTION_HOST = os.getenv("WEB_SERVER_IP_DETECTION_HOST", "8.8.8.8")
WEB_SERVER_IP_DETECTION_PORT = _get_int("WEB_SERVER_IP_DETECTION_PORT", 80)
WEB_SERVER_DEFAULT_LOG_LIMIT = _get_int("WEB_SERVER_DEFAULT_LOG_LIMIT", 150)
WEB_SERVER_DEFAULT_META_LIMIT = _get_int("WEB_SERVER_DEFAULT_META_LIMIT", 50)
WEB_SERVER_DEFAULT_TIMER_DELAY = _get_int("WEB_SERVER_DEFAULT_TIMER_DELAY", 60)
WEB_SERVER_REBOOT_DELAY = _get_float("WEB_SERVER_REBOOT_DELAY", 2.0)
PACIFIC_STANDARD_TIME_OFFSET = _get_int("PACIFIC_STANDARD_TIME_OFFSET", -8)
PACIFIC_DAYLIGHT_TIME_OFFSET = _get_int("PACIFIC_DAYLIGHT_TIME_OFFSET", -7)
GEMINI_MIN_COOLDOWN_SECONDS = _get_int("GEMINI_MIN_COOLDOWN_SECONDS", 5)
GEMINI_DAILY_LIMIT_COOLDOWN = _get_int("GEMINI_DAILY_LIMIT_COOLDOWN", 86400)
RECURSIVE_REPLY_DEPTH_LIMIT = _get_int("RECURSIVE_REPLY_DEPTH_LIMIT", 3)

SITE_STORAGE_LIMIT_DEFAULT = _get_int("SITE_STORAGE_LIMIT_DEFAULT", 10 * 1024 * 1024)
SITE_TIMEOUT_DEFAULT = _get_float("SITE_TIMEOUT_DEFAULT", 5.0)
SITE_ALLOWED_IMPORTS_DEFAULT = os.getenv("SITE_ALLOWED_IMPORTS_DEFAULT", "json,math,random,urllib,hashlib,datetime")
SITE_BLOCKED_IMPORTS_DEFAULT = os.getenv("SITE_BLOCKED_IMPORTS_DEFAULT", "os,sys,subprocess,shutil,builtins")
SITE_ALLOWED_METHODS_DEFAULT = os.getenv("SITE_ALLOWED_METHODS_DEFAULT", "GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS,TRACE,QUERY,CONNECT,PRI")
SITE_BLOCKED_METHODS_DEFAULT = os.getenv("SITE_BLOCKED_METHODS_DEFAULT", "")
SITE_MAX_REQUEST_SIZE_DEFAULT = _get_int("SITE_MAX_REQUEST_SIZE_DEFAULT", 1048576)
SITE_STORAGE_LIMIT_MAX = _get_int("SITE_STORAGE_LIMIT_MAX", 52428800)
SITE_TIMEOUT_MAX = _get_float("SITE_TIMEOUT_MAX", 30.0)

SITE_COMMAND_WHITELIST = os.getenv("SITE_COMMAND_WHITELIST", "all")
SITE_COMMAND_BLACKLIST = os.getenv("SITE_COMMAND_BLACKLIST", "sudo,reboot,shutdown,passwd,chown,chmod")
SITE_COMMAND_REGEX_BLACKLIST = os.getenv("SITE_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|\.session|\.db|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|context_manager\.py|permission_manager\.py|service_manager\.py|command_manager\.py|prompt_interpolator\.py|response_executor\.py|sandbox\.py|registry\.py|utils\.py|parser\.py|downloader\.py|proxy_manager\.py|server\.py|services\.py|main\.py|tools|core|database|services|server|utils|\.txt|\.json)")
SITE_COMMAND_REGEX_WHITELIST = os.getenv("SITE_COMMAND_REGEX_WHITELIST", "")
SITE_PYTHON_WHITELIST = os.getenv("SITE_PYTHON_WHITELIST", "all")
SITE_PYTHON_BLACKLIST = os.getenv("SITE_PYTHON_BLACKLIST", "os.system,os.popen,subprocess,shutil.rmtree,eval,exec")


# =====================================================================
# SECTION 14: CACHE & ASSET FILE NAMES
# =====================================================================
EMOJI_CACHE_DIR_NAME = os.getenv("EMOJI_CACHE_DIR_NAME", "emoji_cache")
AVATAR_CACHE_DIR_NAME = os.getenv("AVATAR_CACHE_DIR_NAME", "avatar_cache")
GIFT_CACHE_DIR_NAME = os.getenv("GIFT_CACHE_DIR_NAME", "gift_cache")
TEMP_MEDIA_DIR_NAME = os.getenv("TEMP_MEDIA_DIR_NAME", "temp_media")
BOT_AVATAR_NAME = os.getenv("BOT_AVATAR_NAME", "bot_avatar.jpg")
DEFAULT_IMAGE_NAME = os.getenv("DEFAULT_IMAGE_NAME", "generated_image.png")
DEFAULT_AUDIO_NAME = os.getenv("DEFAULT_AUDIO_NAME", "generated_audio.mp3")
DEFAULT_VIDEO_NAME = os.getenv("DEFAULT_VIDEO_NAME", "generated_video.mp4")

RE_SEQ_BLOCK = os.getenv("RE_SEQ_BLOCK", r"<(seq|par|bg)>(.*?)</\1>")
RE_REPLY_TAG = os.getenv("RE_REPLY_TAG", r"(?<!\\)\[Reply(?:\s+to\s+message\s+#?|:\s*)(\d+)\]")
RE_REACT_TAG = os.getenv("RE_REACT_TAG", r"(?<!\\)\[React:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_ATTACH_TAG = os.getenv("RE_ATTACH_TAG", r"(?<!\\)\[Attach:\s*([^|\]]+?)\s*(?:\|\s*(.*?))?\s*\]")
RE_EDIT_TAG = os.getenv("RE_EDIT_TAG", r"(?<!\\)\[Edit:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_DELETE_TAG = os.getenv("RE_DELETE_TAG", r"(?<!\\)\[Delete:\s*(\d+)\s*\]")
RE_NOOP_TAG = os.getenv("RE_NOOP_TAG", r"(?<!\\)\[(?:NoOp|No_Op_Ignore|NoOpIgnore):\s*([^|\]]+?)\s*(?:\|\s*continue\s*=\s*(true|false))?\s*\]")
RE_TOOL_TAG = os.getenv("RE_TOOL_TAG", r"(?<!\\)\[Tool:\s*([a-zA-Z0-9_]+)\s*\|\s*(.*?)\s*\]")

_api_keys_raw = os.getenv("WEB_SERVER_API_KEYS", "")
if _api_keys_raw:
    try:
        WEB_SERVER_API_KEYS = json.loads(_api_keys_raw)
    except Exception:
        import secrets
        fallback_key = secrets.token_hex(24)
        WEB_SERVER_API_KEYS = {fallback_key: {"permissions": ["all"], "rate_limit": 100}}
else:
    WEB_SERVER_API_KEYS = {}


# =====================================================================
# TIER 3 & 4 OVERRIDES (RELOAD FROM DB / JSON)
# =====================================================================
if CONFIG_JSON_PATH.exists():
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            local_json = json.load(f)
        for k, v in local_json.items():
            globals()[k] = v
        logger.info("Tier 3 Config Overwrite successfully completed using config.json.")
    except Exception as e:
        logger.error(f"Error loading Tier 3 config.json: {str(e)}")


async def reload_config_from_db(db):
    """Loads dynamic database settings from SQLite and overrides active config parameters in memory."""
    try:
        db_settings = await db.get_all_settings()
        for key, val in db_settings.items():
            try:
                parsed_val = json.loads(val)
            except Exception:
                parsed_val = val
            globals()[key] = parsed_val

        if not globals().get("WEB_SERVER_API_KEYS"):
            import secrets
            saved_key = await db.get_memory("web_server_persistent_admin_token")
            if not saved_key:
                saved_key = secrets.token_hex(24)
                await db.set_memory("web_server_persistent_admin_token", saved_key)
                logger.info(f"CRITICAL SECURITY: Generated secure admin token: {saved_key}")
            
            globals()["WEB_SERVER_API_KEYS"] = {
                saved_key: {"permissions": ["all"], "rate_limit": 100}
            }
        logger.info("Tier 4 Config Overwrite successfully synchronized with database settings!")
    except Exception as e:
        logger.error(f"Error reloading config from DB settings table: {str(e)}")
