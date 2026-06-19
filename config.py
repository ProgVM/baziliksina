# config.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True) # Force dotenv to overwrite cached container variables

# =====================================================================
# SECTION 1: System and Workspace Paths (General Settings)
# =====================================================================
# Project root directory
BASE_DIR = Path(__file__).resolve().parent

# Checking Termux environment and emulated Android storage
is_termux = "com.termux" in sys.executable or "/data/data/com.termux" in str(BASE_DIR)
is_emulated = "emulated" in str(BASE_DIR)

# Safe SQLite database directory allocation (for Termux + emulated storage)
if is_termux or is_emulated:
    SAFE_DB_DIR = Path.home() / ".baziliksina"
    SAFE_DB_DIR.mkdir(parents=True, exist_ok=True)
else:
    SAFE_DB_DIR = BASE_DIR

# Folder for local AI file storage and scratchpads
WORKSPACE_DIR = BASE_DIR / "bot_workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

# File storing the AI's core character and style prompt
CHARACTER_FILE = os.getenv("CHARACTER_FILE", "character.txt")

# Path to the FFmpeg binary used for sticker/voice transcoding
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# Global User-Agent header used to mask outbound network requests
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# =====================================================================
# SECTION 2: Telegram Core and Session Settings
# =====================================================================
# Telegram API credentials (mandatory)
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise ValueError("Please specify TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

try:
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("TELEGRAM_API_ID must be a number")

# Session parameters
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "baziliksina_session")
SESSION_PATH = str(SAFE_DB_DIR / SESSION_NAME)

# numerical Telegram ID of the bot owner/creator
OWNER_ID = int(os.getenv("OWNER_ID", 2113692455))

# Blacklist of Telethon client methods restricted from AI execution
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
# Gemini API keys list loaded from environment variables
gemini_keys_raw = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in gemini_keys_raw.split(",") if k.strip()]

if not GEMINI_KEYS:
    raise ValueError("GEMINI_API_KEYS list is empty. Please specify at least one key in .env")

# List of supported Gemini models
gemini_models_raw = os.getenv("GEMINI_MODELS", "") or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_MODELS = [m.strip() for m in gemini_models_raw.split(",") if m.strip()]

# Model reasoning effort parameter (e.g. 'low', 'medium', 'high')
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "high").lower()

# Sampling temperature (float)
TEMPERATURE = os.getenv("TEMPERATURE", None)
if TEMPERATURE:
    try:
        TEMPERATURE = float(TEMPERATURE)
    except ValueError:
        TEMPERATURE = None

# Nucleus sampling (top_p) (float)
TOP_P = os.getenv("TOP_P", None)
if TOP_P:
    try:
        TOP_P = float(TOP_P)
    except ValueError:
        TOP_P = None

# Custom stop sequences separating turns
STOP_SEQUENCES = [s.strip() for s in os.getenv("STOP_SEQUENCES", "").split(",") if s.strip()]

# Maximum output token limit for generation
OUTPUT_LENGTH = os.getenv("OUTPUT_LENGTH", None)
if OUTPUT_LENGTH:
    try:
        OUTPUT_LENGTH = int(OUTPUT_LENGTH)
    except ValueError:
        OUTPUT_LENGTH = None

# Max allowable input token limit of context window
INPUT_TOKEN_LIMIT = os.getenv("INPUT_TOKEN_LIMIT", None) or os.getenv("MAX_CONTEXT_TOKENS", None)
if INPUT_TOKEN_LIMIT:
    try:
        INPUT_TOKEN_LIMIT = int(INPUT_TOKEN_LIMIT)
    except ValueError:
        INPUT_TOKEN_LIMIT = None

# Safety filter threshold parameters
SAFETY_HATE_SPEECH = os.getenv("SAFETY_HATE_SPEECH", "BLOCK_NONE")
SAFETY_HARASSMENT = os.getenv("SAFETY_HARASSMENT", "BLOCK_NONE")
SAFETY_SEXUALLY_EXPLICIT = os.getenv("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE")
SAFETY_DANGEROUS_CONTENT = os.getenv("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE")


# =====================================================================
# SECTION 4: Generative Media Models (Pollinations Settings)
# =====================================================================
# Pollinations.ai API keys list loaded from environment variables
pollinations_keys_raw = os.getenv("POLLINATIONS_KEYS", "")
POLLINATIONS_KEYS = [k.strip() for k in pollinations_keys_raw.split(",") if k.strip()]

# Image generation defaults
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
DEFAULT_IMAGE_WIDTH = int(os.getenv("DEFAULT_IMAGE_WIDTH", 1024))
DEFAULT_IMAGE_HEIGHT = int(os.getenv("DEFAULT_IMAGE_HEIGHT", 1024))
MEDIA_RESOLUTION = os.getenv("MEDIA_RESOLUTION", "high").lower()
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "auto").lower()

# Audio/TTS synthesis defaults
DEFAULT_AUDIO_VOICE = os.getenv("DEFAULT_AUDIO_VOICE", "nova")
DEFAULT_AUDIO_MODEL = os.getenv("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct")

# Video generation defaults
DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "wan")
DEFAULT_VIDEO_DURATION = int(os.getenv("DEFAULT_VIDEO_DURATION", 5))
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("DEFAULT_VIDEO_ASPECT_RATIO", "1:1")


# =====================================================================
# SECTION 5: Database and Summarization (Memory and Context Settings)
# =====================================================================
# SQLite database filename
DB_NAME = os.getenv("DB_NAME", "bot_context.db")

# SQLite journaling mode (e.g., 'WAL', 'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'OFF')
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL").upper()

# Whether to import past dialog history on empty first cold run
BOOTSTRAP_DATABASE = os.getenv("BOOTSTRAP_DATABASE", "false").lower() == "true"

# Scanning and catch-up bounds for database synchronization
DIALOGS_LIMIT = int(os.getenv("DIALOGS_LIMIT", 50))
BOOTSTRAP_MESSAGES_LIMIT = int(os.getenv("BOOTSTRAP_MESSAGES_LIMIT", 20))
MISSED_MESSAGES_LIMIT = int(os.getenv("MISSED_MESSAGES_LIMIT", 50))

# Anti-race condition network debounce parameters
DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", 7.0))
DUPLICATE_CACHE_SIZE = int(os.getenv("DUPLICATE_CACHE_SIZE", 1000))

# Maximum file size allowed for downloading of media attachments (bytes)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 15 * 1024 * 1024))

# Cache expiration limit for profile pictures/avatars (seconds)
AVATAR_CACHE_TIME = int(os.getenv("AVATAR_CACHE_TIME", 86400))

# Cache subdirectory names inside bot_workspace
EMOJI_CACHE_DIR_NAME = os.getenv("EMOJI_CACHE_DIR_NAME", "emoji_cache")
AVATAR_CACHE_DIR_NAME = os.getenv("AVATAR_CACHE_DIR_NAME", "avatar_cache")
GIFT_CACHE_DIR_NAME = os.getenv("GIFT_CACHE_DIR_NAME", "gift_cache")
TEMP_MEDIA_DIR_NAME = os.getenv("TEMP_MEDIA_DIR_NAME", "temp_media")

# Account avatar filename saved locally in workspace
BOT_AVATAR_NAME = os.getenv("BOT_AVATAR_NAME", "bot_avatar.jpg")

# Context history sizing and local/global ratio parameters
MESSAGES_LIMIT = int(os.getenv("MESSAGES_LIMIT", 150))
CONTEXT_LOCAL_RATIO = float(os.getenv("CONTEXT_LOCAL_RATIO", 0.4))
CONTEXT_LOCAL_MIN_LIMIT = int(os.getenv("CONTEXT_LOCAL_MIN_LIMIT", 15))

# Sizing bounds for database message history summarization
SUMMARIZATION_MESSAGES_LIMIT = int(os.getenv("SUMMARIZATION_MESSAGES_LIMIT", 500))
SUMMARIZATION_KEEP_LIMIT = int(os.getenv("SUMMARIZATION_KEEP_LIMIT", 15))

# Sizing and turn limit caps for context loop transactions
MAX_TURNS = int(os.getenv("MAX_TURNS", 1000))
MEDIA_LIMIT = int(os.getenv("MEDIA_LIMIT", 15))


# =====================================================================
# SECTION 6: Network and Timing Settings (Timeouts and Cooldowns)
# =====================================================================
# Generation loop intervals (seconds)
TIMERS_LOOP_INTERVAL = float(os.getenv("TIMERS_LOOP_INTERVAL", 1.0))
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", 120))
CONNECTION_MONITOR_INTERVAL = int(os.getenv("CONNECTION_MONITOR_INTERVAL", 10))

# Core AI API server latency timeouts and backoff sleeps
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", 60.0))
TYPING_INTERVAL = float(os.getenv("TYPING_INTERVAL", 10.0))
TIMEOUT_SLEEP = float(os.getenv("TIMEOUT_SLEEP", 2.0))
RATE_LIMIT_SLEEP = float(os.getenv("RATE_LIMIT_SLEEP", 5.0))
API_ERROR_SLEEP = float(os.getenv("API_ERROR_SLEEP", 2.0))

# Cooldown limits for API key recovery cycles
GEMINI_FREE_RECOVERY_TIME = int(os.getenv("GEMINI_FREE_RECOVERY_TIME", 18000))
GEMINI_PRO_RECOVERY_TIME = int(os.getenv("GEMINI_PRO_RECOVERY_TIME", 86400))
POLLINATIONS_KEY_RECOVERY_TIME = int(os.getenv("POLLINATIONS_KEY_RECOVERY_TIME", 3600))
KEY_INFO_TIMEOUT = float(os.getenv("KEY_INFO_TIMEOUT", 10.0))

# Periodic user and chat metadata update cycle intervals
PROFILE_UPDATE_INTERVAL = int(os.getenv("PROFILE_UPDATE_INTERVAL", 3600))

# Client action automation timeouts
BOT_RESPONSE_TIMEOUT = float(os.getenv("BOT_RESPONSE_TIMEOUT", 6.0))
DEFAULT_RESULT_INDEX = int(os.getenv("DEFAULT_RESULT_INDEX", 0))
BUTTON_CLICK_TIMEOUT = float(os.getenv("BUTTON_CLICK_TIMEOUT", 15.0))
DOWNLOAD_MEDIA_TIMEOUT = float(os.getenv("DOWNLOAD_MEDIA_TIMEOUT", 120.0))
TELEGRAM_ACTION_TIMEOUT = float(os.getenv("TELEGRAM_ACTION_TIMEOUT", 60.0))
CONVERSION_TIMEOUT = float(os.getenv("CONVERSION_TIMEOUT", 30.0))

# Media cloud generation and provider uploads timeouts
GENERATE_IMAGE_TIMEOUT = float(os.getenv("GENERATE_IMAGE_TIMEOUT", 180.0))
GENERATE_AUDIO_TIMEOUT = float(os.getenv("GENERATE_AUDIO_TIMEOUT", 120.0))
GENERATE_VIDEO_TIMEOUT = float(os.getenv("GENERATE_VIDEO_TIMEOUT", 180.0))
GOOGLE_UPLOAD_TIMEOUT = float(os.getenv("GOOGLE_UPLOAD_TIMEOUT", 120.0))
DEFAULT_PUBLIC_UPLOAD_PROVIDER = os.getenv("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto")
PUBLIC_UPLOAD_TIMEOUT = float(os.getenv("PUBLIC_UPLOAD_TIMEOUT", 60.0))


# =====================================================================
# SECTION 7: Proxy and Anonymization Settings (Tor & Proxy Controls)
# =====================================================================
# Local Tor proxy configurations
TOR_HOST = os.getenv("TOR_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", 9050))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", 9051))
TOR_PASSWORD = os.getenv("TOR_PASSWORD", "")
TOR_ROTATION_TIMEOUT = float(os.getenv("TOR_ROTATION_TIMEOUT", 15.0))

# Fallback proxy rotation thresholds
POLLINATIONS_MAX_ATTEMPTS = int(os.getenv("POLLINATIONS_MAX_ATTEMPTS", 8))
TOR_MAX_CONSECUTIVE_FAILURES = int(os.getenv("TOR_MAX_CONSECUTIVE_FAILURES", 2))
PROXY_CHECK_TIMEOUT = float(os.getenv("PROXY_CHECK_TIMEOUT", "3.0"))

def _parse_proxy_list(key: str) -> list:
    raw = os.getenv(key, "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]

# Segregated, modular proxy pools
PROXY_LIST_TELETHON = _parse_proxy_list("TELEGRAM_PROXIES")
PROXY_LIST_GEMINI = _parse_proxy_list("GEMINI_PROXIES")
PROXY_LIST_POLLINATIONS = _parse_proxy_list("POLLINATIONS_PROXIES")
PROXY_LIST_SCRAPER = _parse_proxy_list("SCRAPER_PROXIES")

raw_proxy_url = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or ""
if raw_proxy_url:
    if not PROXY_LIST_TELETHON: PROXY_LIST_TELETHON = [raw_proxy_url]
    if not PROXY_LIST_GEMINI: PROXY_LIST_GEMINI = [raw_proxy_url]
    if not PROXY_LIST_POLLINATIONS: PROXY_LIST_POLLINATIONS = [raw_proxy_url]
    if not PROXY_LIST_SCRAPER: PROXY_LIST_SCRAPER = [raw_proxy_url]

def check_proxy_active(proxy_url_str: str) -> bool:
    """
    Parses the hostname and port from any proxy URL string (socks5://, http://, etc.)
    and verifies if the specified remote proxy server is active and accepting connections.
    """
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
        # Perform a fast, non-blocking TCP socket connection check
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
    if "ALL_PROXY" in os.environ:
        del os.environ["ALL_PROXY"]
    if "all_proxy" in os.environ:
        del os.environ["all_proxy"]
    ALL_PROXY = None


# =====================================================================
# SECTION 8: Sandbox limits and Page Scrapers
# =====================================================================
# Raw SQL execution restrictions
SQL_SELECT_LIMIT = int(os.getenv("SQL_SELECT_LIMIT", 100))
SQL_STDOUT_CHAR_LIMIT = int(os.getenv("SQL_STDOUT_CHAR_LIMIT", 3500))

# Execution limits of Arbitrary MTProto API methods via client proxy
TELEGRAM_ACTION_CHAR_LIMIT = int(os.getenv("TELEGRAM_ACTION_CHAR_LIMIT", 5000))
TELEGRAM_ACTION_CONFIRM_LIMIT = int(os.getenv("TELEGRAM_ACTION_CONFIRM_LIMIT", 500))

# Character limit for VM background execution console logs
VM_STDOUT_NOTICE_LIMIT = int(os.getenv("VM_STDOUT_NOTICE_LIMIT", 1500))

# Isolated Sandbox file system access blacklists
SANDBOX_BLOCKED_FILES = [f.strip() for f in os.getenv("SANDBOX_BLOCKED_FILES", "bot.py,config.py,db_manager.py,key_manager.py,gemini_manager.py,.env,tools.py,sandbox.py,utils.py,downloader.py,registry.py").split(",") if f.strip()]

# Character limits for console tool returns
SANDBOX_COMMAND_CHAR_LIMIT = int(os.getenv("SANDBOX_COMMAND_CHAR_LIMIT", 3000))

# Media filename defaults saved locally in workspace
DEFAULT_IMAGE_NAME = os.getenv("DEFAULT_IMAGE_NAME", "generated_image.png")
DEFAULT_AUDIO_NAME = os.getenv("DEFAULT_AUDIO_NAME", "generated_audio.mp3")
DEFAULT_VIDEO_NAME = os.getenv("DEFAULT_VIDEO_NAME", "generated_video.mp4")

# Web scraping and page parsing bounds
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
TELEGRAM_TIMEOUT = os.getenv("TELEGRAM_TIMEOUT")
TELEGRAM_TIMEOUT = os.getenv("TELEGRAM_CONNECT_TIMEOUT") or os.getenv("TELEGRAM_TIMEOUT")
TELEGRAM_TIMEOUT = float(TELEGRAM_TIMEOUT) if TELEGRAM_TIMEOUT else 15.0
