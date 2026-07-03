# config.py
import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("Config")
load_dotenv(override=True)

# =====================================================================
# SECTION 1: Workspace and System Paths (General Settings)
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

CHARACTER_FILE = os.getenv("CHARACTER_FILE", "character.txt")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# =====================================================================
# SECTION 2: Telegram Core and Session Settings
# =====================================================================
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise ValueError("Please specify TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

try:
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("TELEGRAM_API_ID must be a number")

SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "baziliksina_session")
SESSION_PATH = str(SAFE_DB_DIR / SESSION_NAME)
OWNER_ID = int(os.getenv("OWNER_ID", 2113692455))

TELEGRAM_METHOD_BLACKLIST = {
    "log_out",
    "delete_account",
    "disconnect",
    "sign_in",
    "send_code_request",
    "switch_account",
}

# =====================================================================
# SECTION 3: Core AI Parameters (Gemini Settings)
# =====================================================================
gemini_keys_raw = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in gemini_keys_raw.split(",") if k.strip()]

if not GEMINI_KEYS:
    raise ValueError("GEMINI_API_KEYS list is empty. Please specify at least one key in .env")

gemini_models_raw = os.getenv("GEMINI_MODELS", "") or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_MODELS = [m.strip() for m in gemini_models_raw.split(",") if m.strip()]

THINKING_LEVEL = os.getenv("THINKING_LEVEL", "high").lower()

TEMPERATURE = os.getenv("TEMPERATURE", None)
if TEMPERATURE:
    try: TEMPERATURE = float(TEMPERATURE)
    except ValueError: TEMPERATURE = None

TOP_P = os.getenv("TOP_P", None)
if TOP_P:
    try: TOP_P = float(TOP_P)
    except ValueError: TOP_P = None

STOP_SEQUENCES = [s.strip() for s in os.getenv("STOP_SEQUENCES", "").split(",") if s.strip()]

OUTPUT_LENGTH = os.getenv("OUTPUT_LENGTH", None)
if OUTPUT_LENGTH:
    try: OUTPUT_LENGTH = int(OUTPUT_LENGTH)
    except ValueError: OUTPUT_LENGTH = None

INPUT_TOKEN_LIMIT = os.getenv("INPUT_TOKEN_LIMIT", None) or os.getenv("MAX_CONTEXT_TOKENS", None)
if INPUT_TOKEN_LIMIT:
    try: INPUT_TOKEN_LIMIT = int(INPUT_TOKEN_LIMIT)
    except ValueError: INPUT_TOKEN_LIMIT = None

SAFETY_HATE_SPEECH = os.getenv("SAFETY_HATE_SPEECH", "BLOCK_NONE")
SAFETY_HARASSMENT = os.getenv("SAFETY_HARASSMENT", "BLOCK_NONE")
SAFETY_SEXUALLY_EXPLICIT = os.getenv("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE")
SAFETY_DANGEROUS_CONTENT = os.getenv("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE")

# =====================================================================
# SECTION 4: Generative Media Models (Pollinations Settings)
# =====================================================================
pollinations_keys_raw = os.getenv("POLLINATIONS_KEYS", "")
POLLINATIONS_KEYS = [k.strip() for k in pollinations_keys_raw.split(",") if k.strip()]

DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
DEFAULT_IMAGE_WIDTH = int(os.getenv("DEFAULT_IMAGE_WIDTH", 1024))
DEFAULT_IMAGE_HEIGHT = int(os.getenv("DEFAULT_IMAGE_HEIGHT", 1024))
MEDIA_RESOLUTION = os.getenv("MEDIA_RESOLUTION", "high").lower()
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "auto").lower()

DEFAULT_AUDIO_VOICE = os.getenv("DEFAULT_AUDIO_VOICE", "nova")
DEFAULT_AUDIO_MODEL = os.getenv("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct")

DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "wan")
DEFAULT_VIDEO_DURATION = int(os.getenv("DEFAULT_VIDEO_DURATION", 5))
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("DEFAULT_VIDEO_ASPECT_RATIO", "1:1")
POLLINATIONS_SEED_MIN = int(os.getenv("POLLINATIONS_SEED_MIN", 1))
POLLINATIONS_SEED_MAX = int(os.getenv("POLLINATIONS_SEED_MAX", 999999999))
POLLINATIONS_UPLOAD_JPEG_QUALITY = int(os.getenv("POLLINATIONS_UPLOAD_JPEG_QUALITY", 95))

# =====================================================================
# SECTION 5: Database and Summarization (Memory and Context Settings)
# =====================================================================
DB_NAME = os.getenv("DB_NAME", "bot_context.db")
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL").upper()

BOOTSTRAP_DATABASE = os.getenv("BOOTSTRAP_DATABASE", "false").lower() == "true"
DIALOGS_LIMIT = int(os.getenv("DIALOGS_LIMIT", 50))
BOOTSTRAP_MESSAGES_LIMIT = int(os.getenv("BOOTSTRAP_MESSAGES_LIMIT", 20))
MISSED_MESSAGES_LIMIT = int(os.getenv("MISSED_MESSAGES_LIMIT", 50))

DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", 7.0))
DUPLICATE_CACHE_SIZE = int(os.getenv("DUPLICATE_CACHE_SIZE", 1000))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 15 * 1024 * 1024))
AVATAR_CACHE_TIME = int(os.getenv("AVATAR_CACHE_TIME", 86400))

EMOJI_CACHE_DIR_NAME = os.getenv("EMOJI_CACHE_DIR_NAME", "emoji_cache")
AVATAR_CACHE_DIR_NAME = os.getenv("AVATAR_CACHE_DIR_NAME", "avatar_cache")
GIFT_CACHE_DIR_NAME = os.getenv("GIFT_CACHE_DIR_NAME", "gift_cache")
TEMP_MEDIA_DIR_NAME = os.getenv("TEMP_MEDIA_DIR_NAME", "temp_media")

BOT_AVATAR_NAME = os.getenv("BOT_AVATAR_NAME", "bot_avatar.jpg")

MESSAGES_LIMIT = int(os.getenv("MESSAGES_LIMIT", 150))
CONTEXT_LOCAL_RATIO = float(os.getenv("CONTEXT_LOCAL_RATIO", 0.4))
CONTEXT_LOCAL_MIN_LIMIT = int(os.getenv("CONTEXT_LOCAL_MIN_LIMIT", 15))

SUMMARIZATION_MESSAGES_LIMIT = int(os.getenv("SUMMARIZATION_MESSAGES_LIMIT", 500))
SUMMARIZATION_KEEP_LIMIT = int(os.getenv("SUMMARIZATION_KEEP_LIMIT", 15))

MAX_TURNS = int(os.getenv("MAX_TURNS", 1000))
MEDIA_LIMIT = int(os.getenv("MEDIA_LIMIT", 15))

# =====================================================================
# SECTION 6: Network and Timing Settings (Timeouts and Cooldowns)
# =====================================================================
TIMERS_LOOP_INTERVAL = float(os.getenv("TIMERS_LOOP_INTERVAL", 1.0))
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", 120))
CONNECTION_MONITOR_INTERVAL = int(os.getenv("CONNECTION_MONITOR_INTERVAL", 10))

GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", 60.0))
TYPING_INTERVAL = float(os.getenv("TYPING_INTERVAL", 10.0))
TIMEOUT_SLEEP = float(os.getenv("TIMEOUT_SLEEP", 2.0))
RATE_LIMIT_SLEEP = float(os.getenv("RATE_LIMIT_SLEEP", 5.0))
API_ERROR_SLEEP = float(os.getenv("API_ERROR_SLEEP", 2.0))

GEMINI_FREE_RECOVERY_TIME = int(os.getenv("GEMINI_FREE_RECOVERY_TIME", 18000))
GEMINI_PRO_RECOVERY_TIME = int(os.getenv("GEMINI_PRO_RECOVERY_TIME", 86400))
POLLINATIONS_KEY_RECOVERY_TIME = int(os.getenv("POLLINATIONS_KEY_RECOVERY_TIME", 3600))
KEY_INFO_TIMEOUT = float(os.getenv("KEY_INFO_TIMEOUT", 10.0))

PROFILE_UPDATE_INTERVAL = int(os.getenv("PROFILE_UPDATE_INTERVAL", 3600))
TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT = int(os.getenv("TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT", 10))

BOT_RESPONSE_TIMEOUT = float(os.getenv("BOT_RESPONSE_TIMEOUT", 6.0))
DEFAULT_RESULT_INDEX = int(os.getenv("DEFAULT_RESULT_INDEX", 0))
BUTTON_CLICK_TIMEOUT = float(os.getenv("BUTTON_CLICK_TIMEOUT", 15.0))
DOWNLOAD_MEDIA_TIMEOUT = float(os.getenv("DOWNLOAD_MEDIA_TIMEOUT", 120.0))
TELEGRAM_ACTION_TIMEOUT = float(os.getenv("TELEGRAM_ACTION_TIMEOUT", 60.0))
CONVERSION_TIMEOUT = float(os.getenv("CONVERSION_TIMEOUT", 30.0))

GENERATE_IMAGE_TIMEOUT = float(os.getenv("GENERATE_IMAGE_TIMEOUT", 180.0))
GENERATE_AUDIO_TIMEOUT = float(os.getenv("GENERATE_AUDIO_TIMEOUT", 120.0))
GENERATE_VIDEO_TIMEOUT = float(os.getenv("GENERATE_VIDEO_TIMEOUT", 180.0))
GOOGLE_UPLOAD_TIMEOUT = float(os.getenv("GOOGLE_UPLOAD_TIMEOUT", 120.0))
DEFAULT_PUBLIC_UPLOAD_PROVIDER = os.getenv("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto")
PUBLIC_UPLOAD_TIMEOUT = float(os.getenv("PUBLIC_UPLOAD_TIMEOUT", 60.0))

# =====================================================================
# SECTION 7: Proxy and Anonymization Settings (Tor & Proxy Controls)
# =====================================================================
TOR_HOST = os.getenv("TOR_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", 9050))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", 9051))
TOR_PASSWORD = os.getenv("TOR_PASSWORD", "")
TOR_ROTATION_TIMEOUT = float(os.getenv("TOR_ROTATION_TIMEOUT", 15.0))

POLLINATIONS_MAX_ATTEMPTS = int(os.getenv("POLLINATIONS_MAX_ATTEMPTS", 8))
TOR_MAX_CONSECUTIVE_FAILURES = int(os.getenv("TOR_MAX_CONSECUTIVE_FAILURES", 2))
PROXY_CHECK_TIMEOUT = float(os.getenv("PROXY_CHECK_TIMEOUT", "3.0"))

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

PROXY_LIST_TELETHON = _parse_list("TELEGRAM_PROXIES")
PROXY_LIST_GEMINI = _parse_list("GEMINI_PROXIES")
PROXY_LIST_POLLINATIONS = _parse_list("POLLINATIONS_PROXIES")
PROXY_LIST_SCRAPER = _parse_list("SCRAPER_PROXIES")

raw_proxy_url = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or ""
if raw_proxy_url:
    if not PROXY_LIST_TELETHON: PROXY_LIST_TELETHON = [raw_proxy_url]
    if not PROXY_LIST_GEMINI: PROXY_LIST_GEMINI = [raw_proxy_url]
    if not PROXY_LIST_POLLINATIONS: PROXY_LIST_POLLINATIONS = [raw_proxy_url]
    if not PROXY_LIST_SCRAPER: PROXY_LIST_SCRAPER = [raw_proxy_url]

def check_proxy_active(proxy_url_str: str) -> bool:
    import socket
    import urllib.parse
    if not proxy_url_str:
        return False
    try:
        parsed = urllib.parse.urlparse(proxy_url_str)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        with socket.create_connection((host, port), timeout=PROXY_CHECK_TIMEOUT):
            return True
    except Exception:
        return False

ACTIVE_TELETHON_PROXIES = [p for p in PROXY_LIST_TELETHON if check_proxy_active(p)]
ACTIVE_GEMINI_PROXIES = [p for p in PROXY_LIST_GEMINI if check_proxy_active(p)]
ACTIVE_POLLINATIONS_PROXIES = [p for p in PROXY_LIST_POLLINATIONS if check_proxy_active(p)]
ACTIVE_SCRAPER_PROXIES = [p for p in PROXY_LIST_SCRAPER if check_proxy_active(p)]

is_tor_enabled = check_proxy_active(f"socks5://{TOR_HOST}:{TOR_SOCKS_PORT}")
is_proxy_enabled = len(ACTIVE_TELETHON_PROXIES) > 0 or len(ACTIVE_GEMINI_PROXIES) > 0

if is_proxy_enabled:
    ALL_PROXY = ACTIVE_TELETHON_PROXIES[0] if ACTIVE_TELETHON_PROXIES else (ACTIVE_GEMINI_PROXIES[0] if ACTIVE_GEMINI_PROXIES else raw_proxy_url)
else:
    if "ALL_PROXY" in os.environ: del os.environ["ALL_PROXY"]
    if "all_proxy" in os.environ: del os.environ["all_proxy"]
    ALL_PROXY = None

# =====================================================================
# SECTION 8: Sandbox limits and Page Scrapers
# =====================================================================
SQL_SELECT_LIMIT = int(os.getenv("SQL_SELECT_LIMIT", 100))
SQL_STDOUT_CHAR_LIMIT = int(os.getenv("SQL_STDOUT_CHAR_LIMIT", 3500))
TELEGRAM_ACTION_CHAR_LIMIT = int(os.getenv("TELEGRAM_ACTION_CHAR_LIMIT", 5000))
TELEGRAM_ACTION_CONFIRM_LIMIT = int(os.getenv("TELEGRAM_ACTION_CONFIRM_LIMIT", 500))
VM_STDOUT_NOTICE_LIMIT = int(os.getenv("VM_STDOUT_NOTICE_LIMIT", 1500))
SANDBOX_BLOCKED_FILES = _parse_list("SANDBOX_BLOCKED_FILES", ["bot.py", "config.py", "db_manager.py", "key_manager.py", "gemini_manager.py", ".env", "tools.py", "sandbox.py", "utils.py", "downloader.py", "registry.py"])
SANDBOX_COMMAND_CHAR_LIMIT = int(os.getenv("SANDBOX_COMMAND_CHAR_LIMIT", 3000))

DEFAULT_IMAGE_NAME = os.getenv("DEFAULT_IMAGE_NAME", "generated_image.png")
DEFAULT_AUDIO_NAME = os.getenv("DEFAULT_AUDIO_NAME", "generated_audio.mp3")
DEFAULT_VIDEO_NAME = os.getenv("DEFAULT_VIDEO_NAME", "generated_video.mp4")

WEB_SEARCH_RESULTS_LIMIT = int(os.getenv("WEB_SEARCH_RESULTS_LIMIT", 5))
SCRAPE_CHAR_LIMIT = int(os.getenv("SCRAPE_CHAR_LIMIT", 4000))
WEB_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", 10.0))
WEB_MEDIA_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", 10.0))
SCRAPE_TIMEOUT = float(os.getenv("SCRAPE_TIMEOUT", 10.0))

TELEGRAM_CONNECTION_RETRIES = os.getenv("TELEGRAM_CONNECTION_RETRIES")
TELEGRAM_CONNECTION_RETRIES = int(TELEGRAM_CONNECTION_RETRIES) if TELEGRAM_CONNECTION_RETRIES else 5
TELEGRAM_RETRY_DELAY = os.getenv("TELEGRAM_RETRY_DELAY")
TELEGRAM_RETRY_DELAY = float(TELEGRAM_RETRY_DELAY) if TELEGRAM_RETRY_DELAY else 5.0
TELEGRAM_AUTO_RECONNECT = os.getenv("TELEGRAM_AUTO_RECONNECT", "true").lower() == "true"
TELEGRAM_TIMEOUT = os.getenv("TELEGRAM_TIMEOUT") or os.getenv("TELEGRAM_CONNECT_TIMEOUT")
TELEGRAM_TIMEOUT = float(TELEGRAM_TIMEOUT) if TELEGRAM_TIMEOUT else 15.0

# =====================================================================
# SECTION 9: AI Generation and Flow Triggers
# =====================================================================
BOOTSTRAP_TRIGGER_GENERATION = os.getenv("BOOTSTRAP_TRIGGER_GENERATION", "true").lower() == "true"
CATCH_UP_TRIGGER_GENERATION = os.getenv("CATCH_UP_TRIGGER_GENERATION", "true").lower() == "true"
USE_SYSTEM_PROMPT = os.getenv("USE_SYSTEM_PROMPT", "true").lower() == "true"

# =====================================================================
# SYSTEM PARAMETERS: ADVANCED MULTI-TIER CONFIGURATION MATRIX
# =====================================================================
AI_RESPONSE_MODE = os.getenv("AI_RESPONSE_MODE", "all").strip().lower()
AI_RESPONSE_TRIGGERS = _parse_list("AI_RESPONSE_TRIGGERS", ["name", "username", "mentioned", "reply_to_me"])

SAVE_INCOMING_MESSAGES = os.getenv("SAVE_INCOMING_MESSAGES", "true").lower() == "true"
SAVE_EDITED_MESSAGES = os.getenv("SAVE_EDITED_MESSAGES", "true").lower() == "true"
SAVE_DELETED_MESSAGES = os.getenv("SAVE_DELETED_MESSAGES", "true").lower() == "true"

SAVE_OUTGOING_NEW_MESSAGES = os.getenv("SAVE_OUTGOING_NEW_MESSAGES", "true").lower() == "true"
SAVE_OUTGOING_EDITED_MESSAGES = os.getenv("SAVE_OUTGOING_EDITED_MESSAGES", "true").lower() == "true"
SAVE_OUTGOING_DELETED_MESSAGES = os.getenv("SAVE_OUTGOING_DELETED_MESSAGES", "true").lower() == "true"

TRIGGER_ON_INCOMING = os.getenv("TRIGGER_ON_INCOMING", "true").lower() == "true"
TRIGGER_ON_EDITED = os.getenv("TRIGGER_ON_EDITED", "false").lower() == "true"
TRIGGER_ON_DELETED = os.getenv("TRIGGER_ON_DELETED", "false").lower() == "true"

TRIGGER_ON_OUTGOING_NEW_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_NEW_MESSAGES", "false").lower() == "true"
TRIGGER_ON_OUTGOING_EDITED_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_EDITED_MESSAGES", "false").lower() == "true"
TRIGGER_ON_OUTGOING_DELETED_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_DELETED_MESSAGES", "false").lower() == "true"

ALLOWED_MESSAGE_TYPES = _parse_list("ALLOWED_MESSAGE_TYPES", ["text", "photo", "video", "voice", "audio", "poll", "sticker", "gif", "location", "document"])
FILTER_POLICY = os.getenv("FILTER_POLICY", "blacklist_first").strip().lower()

MSG_SAVE_WHITELIST = _parse_list("MSG_SAVE_WHITELIST", [])
MSG_SAVE_BLACKLIST = _parse_list("MSG_SAVE_BLACKLIST", [])
MSG_GEN_WHITELIST = _parse_list("MSG_GEN_WHITELIST", [])
MSG_GEN_BLACKLIST = _parse_list("MSG_GEN_BLACKLIST", [])

SAVE_INCOMING_REACTION_ADD = os.getenv("SAVE_INCOMING_REACTION_ADD", "true").lower() == "true"
SAVE_INCOMING_REACTION_REMOVE = os.getenv("SAVE_INCOMING_REACTION_REMOVE", "true").lower() == "true"
SAVE_OUTGOING_REACTION_ADD = os.getenv("SAVE_OUTGOING_REACTION_ADD", "true").lower() == "true"
SAVE_OUTGOING_REACTION_REMOVE = os.getenv("SAVE_OUTGOING_REACTION_REMOVE", "true").lower() == "true"

TRIGGER_ON_INCOMING_REACTION_ADD = os.getenv("TRIGGER_ON_INCOMING_REACTION_ADD", "false").lower() == "true"
TRIGGER_ON_INCOMING_REACTION_REMOVE = os.getenv("TRIGGER_ON_INCOMING_REACTION_REMOVE", "false").lower() == "true"
TRIGGER_ON_OUTGOING_REACTION_ADD = os.getenv("TRIGGER_ON_OUTGOING_REACTION_ADD", "false").lower() == "true"
TRIGGER_ON_OUTGOING_REACTION_REMOVE = os.getenv("TRIGGER_ON_OUTGOING_REACTION_REMOVE", "false").lower() == "true"

REACTION_WHITELIST = _parse_list("REACTION_WHITELIST", [])
REACTION_BLACKLIST = _parse_list("REACTION_BLACKLIST", [])

SAVE_USER_METADATA = os.getenv("SAVE_USER_METADATA", "true").lower() == "true"
SAVE_CHAT_METADATA = os.getenv("SAVE_CHAT_METADATA", "true").lower() == "true"

USER_CACHE_WHITELIST = _parse_int_list("USER_CACHE_WHITELIST", [])
USER_CACHE_BLACKLIST = _parse_int_list("USER_CACHE_BLACKLIST", [])
CHAT_CACHE_WHITELIST = _parse_int_list("CHAT_CACHE_WHITELIST", [])
CHAT_CACHE_BLACKLIST = _parse_int_list("CHAT_CACHE_BLACKLIST", [])

CHAT_WHITELIST = _parse_int_list("CHAT_WHITELIST", [])
CHAT_BLACKLIST = _parse_int_list("CHAT_BLACKLIST", [])

AI_OUTPUT_WHITELIST_REGEX = _parse_list("AI_OUTPUT_WHITELIST_REGEX", [])
AI_OUTPUT_BLACKLIST_REGEX = _parse_list("AI_OUTPUT_BLACKLIST_REGEX", [])

AI_ALLOWED_ROOT_TOOLS = _parse_list("AI_ALLOWED_ROOT_TOOLS", ["all"])
AI_BLOCKED_ROOT_TOOLS = _parse_list("AI_BLOCKED_ROOT_TOOLS", ["execute_python_code", "run_sandboxed_command"])
AI_ALLOWED_CUSTOM_TOOLS = _parse_list("AI_ALLOWED_CUSTOM_TOOLS", ["all"])
AI_BLOCKED_CUSTOM_TOOLS = _parse_list("AI_BLOCKED_CUSTOM_TOOLS", [])

CUSTOM_TOOLS_ENABLE = os.getenv("CUSTOM_TOOLS_ENABLE", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_VIEW = os.getenv("CUSTOM_TOOLS_ALLOW_VIEW", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_CREATE = os.getenv("CUSTOM_TOOLS_ALLOW_CREATE", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_EDIT = os.getenv("CUSTOM_TOOLS_ALLOW_EDIT", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_DELETE = os.getenv("CUSTOM_TOOLS_ALLOW_DELETE", "true").lower() == "true"
CUSTOM_TOOLS_INVOKE_POLICY = os.getenv("CUSTOM_TOOLS_INVOKE_POLICY", "all").strip().lower()

CUSTOM_TOOLS_INVOKE_WHITELIST = _parse_int_list("CUSTOM_TOOLS_INVOKE_WHITELIST", [])
CUSTOM_TOOLS_INVOKE_BLACKLIST = _parse_int_list("CUSTOM_TOOLS_INVOKE_BLACKLIST", [])

CROSS_CHAT_CONTEXT = os.getenv("CROSS_CHAT_CONTEXT", "true").lower() == "true"
STREAMING_GENERATION = os.getenv("STREAMING_GENERATION", "false").lower() == "true"
STREAMING_INTERVAL = float(os.getenv("STREAMING_INTERVAL", 1.5))
MESSAGE_POOL_LIMIT = int(os.getenv("MESSAGE_POOL_LIMIT", 50))
PENDING_QUEUE_LIMIT = int(os.getenv("PENDING_QUEUE_LIMIT", 10))
TEMP_MEDIA_CLEANUP_INTERVAL = float(os.getenv("TEMP_MEDIA_CLEANUP_INTERVAL", 3600.0))

RE_SEQ_BLOCK = os.getenv("RE_SEQ_BLOCK", r"<(seq|par|bg)>(.*?)</\1>")
RE_REPLY_TAG = os.getenv("RE_REPLY_TAG", r"(?<!\\)\[Reply(?:\s+to\s+message\s+#?|:\s*)(\d+)\]")
RE_REACT_TAG = os.getenv("RE_REACT_TAG", r"(?<!\\)\[React:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_ATTACH_TAG = os.getenv("RE_ATTACH_TAG", r"(?<!\\)\[Attach:\s*([^|\]]+?)\s*(?:\|\s*(.*?))?\s*\]")
RE_EDIT_TAG = os.getenv("RE_EDIT_TAG", r"(?<!\\)\[Edit:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_DELETE_TAG = os.getenv("RE_DELETE_TAG", r"(?<!\\)\[Delete:\s*(\d+)\s*\]")
RE_NOOP_TAG = os.getenv("RE_NOOP_TAG", r"(?<!\\)\[NoOp:\s*([^|\]]+?)\s*(?:\|\s*continue\s*=\s*(true|false))?\s*\]")
RE_TOOL_TAG = os.getenv("RE_TOOL_TAG", r"(?<!\\)\[Tool:\s*([a-zA-Z0-9_]+)\s*\|\s*(.*?)\s*\]")

# =====================================================================
# WEB SERVER SYSTEM PARAMETERS (DYNAMIC MULTI-TIER CONFIG)
# =====================================================================
WEB_SERVER_ENABLE = os.getenv("WEB_SERVER_ENABLE", "true").lower() == "true"
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", 8080))
WEB_SERVER_SUBDOMAIN = os.getenv("WEB_SERVER_SUBDOMAIN", "").strip()
WEB_SERVER_LOG_PATH = os.getenv("WEB_SERVER_LOG_PATH", "bot.log")
WEB_SERVER_IP_ACL = _parse_list("WEB_SERVER_IP_ACL", [])
WEB_SERVER_IP_DETECTION_HOST = os.getenv("WEB_SERVER_IP_DETECTION_HOST", "8.8.8.8")
WEB_SERVER_IP_DETECTION_PORT = int(os.getenv("WEB_SERVER_IP_DETECTION_PORT", 80))
WEB_SERVER_DEFAULT_LOG_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_LOG_LIMIT", 150))
WEB_SERVER_DEFAULT_META_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_META_LIMIT", 50))
WEB_SERVER_DEFAULT_LOG_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_LOG_LIMIT", 150))
WEB_SERVER_DEFAULT_META_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_META_LIMIT", 50))
WEB_SERVER_DEFAULT_TIMER_DELAY = int(os.getenv("WEB_SERVER_DEFAULT_TIMER_DELAY", 60))
WEB_SERVER_REBOOT_DELAY = float(os.getenv("WEB_SERVER_REBOOT_DELAY", 2.0))
PACIFIC_STANDARD_TIME_OFFSET = int(os.getenv("PACIFIC_STANDARD_TIME_OFFSET", -8))
PACIFIC_DAYLIGHT_TIME_OFFSET = int(os.getenv("PACIFIC_DAYLIGHT_TIME_OFFSET", -7))
GEMINI_MIN_COOLDOWN_SECONDS = int(os.getenv("GEMINI_MIN_COOLDOWN_SECONDS", 5))
GEMINI_DAILY_LIMIT_COOLDOWN = int(os.getenv("GEMINI_DAILY_LIMIT_COOLDOWN", 86400))

SANDBOX_CONFIG_WHITELIST = _parse_list("SANDBOX_CONFIG_WHITELIST", ["all"])
SANDBOX_CONFIG_BLACKLIST = _parse_list("SANDBOX_CONFIG_BLACKLIST", ["API_HASH", "TELEGRAM_API_HASH", "GEMINI_API_KEYS", "GEMINI_KEYS", "POLLINATIONS_KEYS", "TOR_PASSWORD", "ALL_PROXY", "all_proxy", "TELEGRAM_PROXIES", "GEMINI_PROXIES", "POLLINATIONS_PROXIES", "SCRAPER_PROXIES"])

GAME_EMOJI_WHITELIST = _parse_list("GAME_EMOJI_WHITELIST", ["🎲", "🎯", "🎳", "🏀", "⚽", "🎰"])
GAME_EMOJI_BLACKLIST = _parse_list("GAME_EMOJI_BLACKLIST", [])

SANDBOX_COMMAND_WHITELIST = _parse_list("SANDBOX_COMMAND_WHITELIST", ["all"])
SANDBOX_COMMAND_BLACKLIST = _parse_list("SANDBOX_COMMAND_BLACKLIST", ["rm", "sudo", "reboot", "shutdown", "init", "passwd", "chown", "chmod", "dd", "mkfs", "parted", "fdisk", "mkswap", "killall", "pkill", "kill", "mv", "systemctl", "service"])

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

_api_keys_raw = os.getenv("WEB_SERVER_API_KEYS", "")
if _api_keys_raw:
    try:
        WEB_SERVER_API_KEYS = json.loads(_api_keys_raw)
    except Exception:
        # Avoid static backdoors, generate random persistent admin key at startup
        import secrets
        fallback_key = secrets.token_hex(24)
        logger.warning(f"CRITICAL: Failed to parse WEB_SERVER_API_KEYS. Generated secure random fallback token: {fallback_key}")
        WEB_SERVER_API_KEYS = {
            fallback_key: {"permissions": ["all"], "rate_limit": 100}
        }
else:
    # Key dictionary is populated during runtime DB loading to prevent backdoor leaks
    WEB_SERVER_API_KEYS = {}


# =====================================================================
# TIER 3 & 4 RUNTIME LOGIC: Overwrite with config.json and SQLite settings
# =====================================================================
CONFIG_JSON_PATH = BASE_DIR / "config" / "config.json"
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
        
        # Security self-healing: if no custom keys exist, generate persistent random secure administrative token
        if not globals().get("WEB_SERVER_API_KEYS"):
            import secrets
            saved_key = await db.get_memory("web_server_persistent_admin_token")
            if not saved_key:
                saved_key = secrets.token_hex(24)
                await db.set_memory("web_server_persistent_admin_token", saved_key)
                logger.info(f"CRITICAL SECURITY: First start, no keys provided. Generated secure administrative token: {saved_key}")
            
            globals()["WEB_SERVER_API_KEYS"] = {
                saved_key: {"permissions": ["all"], "rate_limit": 100}
            }
        logger.info("Tier 4 Config Overwrite successfully synchronized with database settings!")
    except Exception as e:
        logger.error(f"Error reloading config from DB settings table: {str(e)}")
