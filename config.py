# config.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project root directory
BASE_DIR = Path(__file__).resolve().parent

# Checking Termux environment and emulated Android storage
is_termux = "com.termux" in sys.executable or "/data/data/com.termux" in str(BASE_DIR)
is_emulated = "emulated" in str(BASE_DIR)

# For safe SQLite (Telethon + our context)
if is_termux or is_emulated:
    SAFE_DB_DIR = Path.home() / ".baziliksina"
    SAFE_DB_DIR.mkdir(parents=True, exist_ok=True)
else:
    SAFE_DB_DIR = BASE_DIR

# Folder for local AI file storage
WORKSPACE_DIR = BASE_DIR / "bot_workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

# Loading and verifying Telegram API
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise ValueError("Please specify TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

try:
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("TELEGRAM_API_ID must be a number")

# Session name
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "baziliksina_session")
SESSION_PATH = str(SAFE_DB_DIR / SESSION_NAME)

# Owner ID
OWNER_ID = int(os.getenv("OWNER_ID", 2113692455))

# Loading Google Gemini API keys
gemini_keys_raw = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in gemini_keys_raw.split(",") if k.strip()]

if not GEMINI_KEYS:
    raise ValueError("GEMINI_API_KEYS list is empty. Please specify at least one key in .env")

# Loading API keys from the PollinationsAI gateway
pollinations_keys_raw = os.getenv("POLLINATIONS_KEYS", "")
POLLINATIONS_KEYS = [k.strip() for k in pollinations_keys_raw.split(",") if k.strip()]

# Support for a comma-separated list of Gemini models
gemini_models_raw = os.getenv("GEMINI_MODELS", "") or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_MODELS = [m.strip() for m in gemini_models_raw.split(",") if m.strip()]

# Additional Gemini API generation parameters
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "high").lower()
TEMPERATURE = os.getenv("TEMPERATURE", None)
if TEMPERATURE:
    try:
        TEMPERATURE = float(TEMPERATURE)
    except ValueError:
        TEMPERATURE = None

# Nucleus sampling (Top-P)
TOP_P = os.getenv("TOP_P", None)
if TOP_P:
    try:
        TOP_P = float(TOP_P)
    except ValueError:
        TOP_P = None

STOP_SEQUENCES = [s.strip() for s in os.getenv("STOP_SEQUENCES", "").split(",") if s.strip()]
OUTPUT_LENGTH = os.getenv("OUTPUT_LENGTH", None)
if OUTPUT_LENGTH:
    try:
        OUTPUT_LENGTH = int(OUTPUT_LENGTH)
    except ValueError:
        OUTPUT_LENGTH = None

# Renamed to INPUT_TOKEN_LIMIT
INPUT_TOKEN_LIMIT = os.getenv("INPUT_TOKEN_LIMIT", None) or os.getenv("MAX_CONTEXT_TOKENS", None)
if INPUT_TOKEN_LIMIT:
    try:
        INPUT_TOKEN_LIMIT = int(INPUT_TOKEN_LIMIT)
    except ValueError:
        INPUT_TOKEN_LIMIT = None

# Visualization parameters
MEDIA_RESOLUTION = os.getenv("MEDIA_RESOLUTION", "high").lower()
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "auto").lower()

# Blacklist of Telethon methods for AI (for security purposes)
TELEGRAM_METHOD_BLACKLIST = {
    "log_out",
    "delete_account",
    "disconnect",
    "sign_in",
    "send_code_request",
    "switch_account",
}

# Whether to bootstrap existing data for the database on the first run
BOOTSTRAP_DATABASE = os.getenv("BOOTSTRAP_DATABASE", "false").lower() == "true"

# Limits for scanning dialogs and messages for background services
DIALOGS_LIMIT = int(os.getenv("DIALOGS_LIMIT", 50))
BOOTSTRAP_MESSAGES_LIMIT = int(os.getenv("BOOTSTRAP_MESSAGES_LIMIT", 20))
MISSED_MESSAGES_LIMIT = int(os.getenv("MISSED_MESSAGES_LIMIT", 50))

# Settings for debounce, media file caching, and deduplicator
DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", 7.0))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 15 * 1024 * 1024))   # 15 MB by default
AVATAR_CACHE_TIME = int(os.getenv("AVATAR_CACHE_TIME", 86400))     # 24 hours by default (in seconds)
DUPLICATE_CACHE_SIZE = int(os.getenv("DUPLICATE_CACHE_SIZE", 1000))

# Limit of messages loaded into active history (default: 150)
MESSAGES_LIMIT = int(os.getenv("MESSAGES_LIMIT", 150))

# Context window allocation parameters (ratio and minimum messages for active chat)
CONTEXT_LOCAL_RATIO = float(os.getenv("CONTEXT_LOCAL_RATIO", 0.4))
CONTEXT_LOCAL_MIN_LIMIT = int(os.getenv("CONTEXT_LOCAL_MIN_LIMIT", 15))

# Limit for loading messages from DB for end-to-end context summarization (default: 500)
SUMMARIZATION_MESSAGES_LIMIT = int(os.getenv("SUMMARIZATION_MESSAGES_LIMIT", 500))

# Parameter for the limit of messages kept during context summarization
SUMMARIZATION_KEEP_LIMIT = int(os.getenv("SUMMARIZATION_KEEP_LIMIT", 15))

# Limit of multi-step generation turns (default: 1000)
MAX_TURNS = int(os.getenv("MAX_TURNS", 1000))

# Limit of simultaneously loaded media files in the history context (default: 15)
MEDIA_LIMIT = int(os.getenv("MEDIA_LIMIT", 15))

# Safety filter threshold settings for 4 categories
SAFETY_HATE_SPEECH = os.getenv("SAFETY_HATE_SPEECH", "BLOCK_NONE")
SAFETY_HARASSMENT = os.getenv("SAFETY_HARASSMENT", "BLOCK_NONE")
SAFETY_SEXUALLY_EXPLICIT = os.getenv("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE")
SAFETY_DANGEROUS_CONTENT = os.getenv("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE")

# Timeouts and sleep delays for the AI core
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", 60.0))          # Gemini response generation timeout
TYPING_INTERVAL = float(os.getenv("TYPING_INTERVAL", 10.0))        # Frequency of sending the "typing..." status
TIMEOUT_SLEEP = float(os.getenv("TIMEOUT_SLEEP", 2.0))             # Pause on timeout waiting for model response
RATE_LIMIT_SLEEP = float(os.getenv("RATE_LIMIT_SLEEP", 5.0))       # Pause before rotation on 429 limit
API_ERROR_SLEEP = float(os.getenv("API_ERROR_SLEEP", 2.0))         # Pause on Google server errors (502/503/504)

# Interval for periodic background updates of user profiles and chats (default: 3600 seconds / 1 hour)
PROFILE_UPDATE_INTERVAL = int(os.getenv("PROFILE_UPDATE_INTERVAL", 3600))

# Timeouts for Telegram action automation
BOT_RESPONSE_TIMEOUT = float(os.getenv("BOT_RESPONSE_TIMEOUT", 6.0))
DEFAULT_RESULT_INDEX = int(os.getenv("DEFAULT_RESULT_INDEX", 0))
BUTTON_CLICK_TIMEOUT = float(os.getenv("BUTTON_CLICK_TIMEOUT", 15.0))
DOWNLOAD_MEDIA_TIMEOUT = float(os.getenv("DOWNLOAD_MEDIA_TIMEOUT", 120.0))
TELEGRAM_ACTION_TIMEOUT = float(os.getenv("TELEGRAM_ACTION_TIMEOUT", 60.0))

# Network parameters and ports of the Tor control socket
TOR_HOST = os.getenv("TOR_HOST", "127.0.0.1")                      # Tor network local host
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", 9050))            # Tor network SOCKS port (standard: 9050)
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", 9051))        # Tor control port (standard: 9051)
TOR_PASSWORD = os.getenv("TOR_PASSWORD", "")                        # Authorization password for the Tor control socket
TOR_ROTATION_TIMEOUT = float(os.getenv("TOR_ROTATION_TIMEOUT", 15.0)) # Timeout limit when sending NEWNYM to Tor

# Fine-tuning of rotation limits and API network attempts
POLLINATIONS_MAX_ATTEMPTS = int(os.getenv("POLLINATIONS_MAX_ATTEMPTS", 8))
TOR_MAX_CONSECUTIVE_FAILURES = int(os.getenv("TOR_MAX_CONSECUTIVE_FAILURES", 2))

# System limits for relational analysis and database SQL control
SQL_SELECT_LIMIT = int(os.getenv("SQL_SELECT_LIMIT", 100))
SQL_STDOUT_CHAR_LIMIT = int(os.getenv("SQL_STDOUT_CHAR_LIMIT", 3500))

# Limits for page scraping and web search
WEB_SEARCH_RESULTS_LIMIT = int(os.getenv("WEB_SEARCH_RESULTS_LIMIT", 5))
SCRAPE_CHAR_LIMIT = int(os.getenv("SCRAPE_CHAR_LIMIT", 4000))

# Global User-Agent for network request masking
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Path to the ffmpeg binary for Termux/Linux
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# Name of the text file containing the character personality
CHARACTER_FILE = os.getenv("CHARACTER_FILE", "character.txt")

# Limit recovery cooldowns for different categories of Gemini and PollinationsAI models, and timeouts (in seconds)
GEMINI_FREE_RECOVERY_TIME = int(os.getenv("GEMINI_FREE_RECOVERY_TIME", 18000))  # 5 hours by default
GEMINI_PRO_RECOVERY_TIME = int(os.getenv("GEMINI_PRO_RECOVERY_TIME", 86400))    # 24 hours by default
POLLINATIONS_KEY_RECOVERY_TIME = int(os.getenv("POLLINATIONS_KEY_RECOVERY_TIME", 3600)) # 1 hour
KEY_INFO_TIMEOUT = float(os.getenv("KEY_INFO_TIMEOUT", 10.0))

# Timeout limit for asynchronous transcoding of stickers/voice (in seconds)
CONVERSION_TIMEOUT = float(os.getenv("CONVERSION_TIMEOUT", 30.0))

# Update intervals for background loops
TIMERS_LOOP_INTERVAL = float(os.getenv("TIMERS_LOOP_INTERVAL", 1.0)) # Polling interval for SQLite timers
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", 120))     # 'online' ping interval
CONNECTION_MONITOR_INTERVAL = int(os.getenv("CONNECTION_MONITOR_INTERVAL", 10)) # Network check interval

# Default parameters for web search and scraping utilities (used in tools.py signatures)
WEB_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", 10.0))
WEB_MEDIA_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", 10.0))
SCRAPE_TIMEOUT = float(os.getenv("SCRAPE_TIMEOUT", 10.0))

# Default parameters for the PollinationsAI image generator (used in tools.py signatures)
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
DEFAULT_IMAGE_WIDTH = int(os.getenv("DEFAULT_IMAGE_WIDTH", 1024))
DEFAULT_IMAGE_HEIGHT = int(os.getenv("DEFAULT_IMAGE_HEIGHT", 1024))
GENERATE_IMAGE_TIMEOUT = float(os.getenv("GENERATE_IMAGE_TIMEOUT", 180.0))

# Default parameters for the PollinationsAI audio generator (used in tools.py signatures)
DEFAULT_AUDIO_VOICE = os.getenv("DEFAULT_AUDIO_VOICE", "nova")
DEFAULT_AUDIO_MODEL = os.getenv("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct")
GENERATE_AUDIO_TIMEOUT = float(os.getenv("GENERATE_AUDIO_TIMEOUT", 120.0))

# Default parameters for the PollinationsAI video generator (used in tools.py signatures)
DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "wan")
DEFAULT_VIDEO_DURATION = int(os.getenv("DEFAULT_VIDEO_DURATION", 5))
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("DEFAULT_VIDEO_ASPECT_RATIO", "1:1")
GENERATE_VIDEO_TIMEOUT = float(os.getenv("GENERATE_VIDEO_TIMEOUT", 180.0))

# Default parameters for cloud media upload (used in tools.py signatures)
GOOGLE_UPLOAD_TIMEOUT = float(os.getenv("GOOGLE_UPLOAD_TIMEOUT", 120.0))
DEFAULT_PUBLIC_UPLOAD_PROVIDER = os.getenv("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto")
PUBLIC_UPLOAD_TIMEOUT = float(os.getenv("PUBLIC_UPLOAD_TIMEOUT", 60.0))
