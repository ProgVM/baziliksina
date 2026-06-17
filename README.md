# Baziliksina Userbot

**Baziliksina** is a modular, high-performance AI agent (Telegram Userbot) built on top of the MTProto-client **Telethon** and natively powered by **Google Gemini API** models and the **Pollinations.ai** gateway.

Designed as an autonomous system, Baziliksina can deeply analyze multimodal chat contexts (such as images, videos, audio recordings, and voice notes), run sandboxed asynchronous Python scripts, query and modify a local SQLite database, schedule task timers, configure auto-wake triggers, and dynamically compile new custom AI tools at runtime.

---

## Architectural Breakdown (12 Core Modules)

The project is structured into 12 logically isolated Python modules:

### 1. `bot.py` (Core Listener)
* **Role:** The entry point of the program. It instantiates the Telethon MTProto client and handles network event dispatching.
* **Key Components:**
  * Event handlers for new messages (`NewMessage`), edited messages (`MessageEdited`), deleted messages (`MessageDeleted`), and message reactions.
  * An incremental-counter-based debounce algorithm that prevents double-sending or parallel generations during rapid user messaging.
  * Background keep-alive and network connection monitor services.

### 2. `config.py` (Central Configuration)
* **Role:** Manages, categorizes, and validates all environment variables and configuration constants.
* **Structure:** Organized into 8 cohesive sections:
  1. System and Workspace Paths (General Settings)
  2. Telegram Core and Session Settings
  3. Core AI Parameters (Gemini Settings)
  4. Generative Media Models (Pollinations Settings)
  5. Database and Summarization (Memory and Context Settings)
  6. Network and Timing Settings (Timeouts and Cooldowns)
  7. Proxy and Anonymization Settings (Tor & Proxy Controls)
  8. Sandbox Limits and Page Scrapers

### 3. `db_manager.py` (Database Engine)
* **Role:** An asynchronous SQLite database wrapper designed on top of `aiosqlite`.
* **Key Components:**
  * Connects and optimizes SQLite transactions utilizing configurable journaling modes (such as WAL, DELETE, MEMORY) defined in config.
  * Manages 10 database tables including message history, accompanying secondary metadata, shared memory, persistent triggers, profile caches, and dynamic tools.
  * Features split local/global context retrieval (`get_history`) to keep the model focused on active chat threads while remaining globally aware of other conversations.

### 4. `downloader.py` (Multimedia Transcoder)
* **Role:** Handles asynchronous downloading of message attachments and performs format conversions to ensure Gemini API compatibility.
* **Key Components:**
  * Downloader routines for attachments, voice notes, video circles, star gifts, and avatars.
  * On-the-fly transcoding of transparent WebM custom emojis/stickers to H.264 `.mp4` using FFmpeg.
  * Transcoding of Opus `.ogg` voice notes to standard `.mp3` using FFmpeg for native Gemini hearing.

### 5. `gemini_manager.py` (AI Orchestrator)
* **Role:** Handles turn-based generation transactions, tool execution, and prompt assembly.
* **Key Components:**
  * Dynamic assembly of system instructions based on premium profile caches.
  * Multi-turn tool execution loop resolving both native and healed function calls.
  * An Auto-Heal Interceptor that parses stringified function calls in conversational responses and converts them into executable `FunctionCall` objects.
  * Regular-expression filters that programmatically strip leaked metadata headers and reasoning thought blocks (`thought`).

### 6. `key_manager.py` (API Rotation & Limits)
* **Role:** Restores, rotates, and manages quotas for Gemini and Pollinations API keys.
* **Key Components:**
  * `GeminiKeyManager` featuring automatic model rotation (Flash vs Pro) and distinct cooldown timers (5 hours for Flash, 24 hours for Pro models) on 429 exhaustion.
  * `PollinationsKeyManager` featuring batch blocking of all keys belonging to an owner upon quota exhaustion.

### 7. `parser.py` (Structure Analyzer)
* **Role:** Dissects complex raw MTProto Telegram objects into visual and text representations.
* **Key Components:**
  * Reconstructs full Markdown formatting (bold, italic, spoilers, code blocks) on incoming messages by accessing `message.text`.
  * Extracts custom emojis, reactions, Star Gift details, and cross-chat reply/quote offsets.
  * Populates cache tables for premium users and channel profiles.

### 8. `registry.py` (Tool Registry)
* **Role:** A thread-safe catalog storing all executable AI functions.
* **Key Components:**
  * Class-based function mapper (`FunctionRegistry` singleton).
  * Runtime compiler `compile_custom_tool` compiling sandboxed functions created dynamically by the model and instantly registering them in memory.

### 9. `sandbox.py` (Virtual Machine)
* **Role:** Hosts the execution of untrusted AI-generated Python code.
* **Key Components:**
  * `SandboxedClient` proxies File and Network methods of Telethon to protect systemic files.
  * `AsyncSandbox` executing code in an isolated dictionary scope with a protected, chroot-like override of the built-in `open()` function.

### 10. `services.py` (Background Services)
* **Role:** Keeps the userbot alive and handles history synchronization.
* **Key Components:**
  * Status ping services keeping the account online.
  * Missed messages sync services (`catch_up_missed_messages`) populating the database after network drops or startup, scheduling automatic debounce replies where necessary.

### 11. `tools.py` (AI Toolset)
* **Role:** Exposes 33 powerful systemic tools to the model.
* **Key Components:**
  * Core File system management (workspace files, url downloader, Telegraph, file.io upload).
  * Internet search engines and scrapers.
  * Telegram automation actions (agent replies, button clicking, inline queries).
  * Timers and auto-wake triggers.
  * Media generators (
flux image, wan video, qwen-tts audio).
  * SQL transaction executors and sandbox VMs.
  * Ultimate Telegram object and message analyzers returning 100% complete raw MTProto JSON payloads.

### 12. `utils.py` (JSON Serializers)
* **Role:** Handles deep serialization of custom objects.
* **Key Components:**
  * `TelegramJSONEncoder` translating custom Telethon types, sets, bytes, and dates recursively into serializeable format.
  * File-path sanitizers protecting workspace downloads.

---

## Configuration Variables (`.env`)

Configure the environment by creating a `.env` file in the root directory:

```ini
# --- TELEGRAM API CREDENTIALS ---
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION_NAME=baziliksina_session
OWNER_ID=your_telegram_id

# --- SQLITE ENGINE SETTINGS ---
SQLITE_JOURNAL_MODE=WAL

# --- GOOGLE GEMINI CONFIGURATION ---
GEMINI_API_KEYS=key1,key2,key3
GEMINI_MODELS=gemini-2.5-flash,gemini-2.5-pro

# --- POLLINATIONS AI CONFIGURATION ---
POLLINATIONS_KEYS=pk_key1,sk_key2

# --- LIMITS AND TIMEOUTS ---
TELEGRAM_ACTION_CHAR_LIMIT=5000
```

---

## Setup and Installation

### 1. Install System Dependencies
Ensure **Python 3.10+**, **FFmpeg**, and **Tor** are installed:

*   **Termux (Android):**
    ```bash
    pkg update && pkg upgrade -y
    pkg install python ffmpeg tor git -y
    termux-setup-storage
    ```
*   **Linux (Debian/Ubuntu):**
    ```bash
    sudo apt update && sudo apt upgrade -y
    sudo apt install python3 python3-pip ffmpeg tor git -y
    ```

### 2. Install Project Requirements
```bash
git clone https://github.com/yourusername/baziliksina.git
cd baziliksina
pip install -r requirements.txt
```

### 3. Start Tor Service (Optional)
Enable Tor SOCKS control port in `torrc` and run:
```bash
tor &
```

### 4. Authenticate and Run the Userbot
```bash
python bot.py
```
Enter your phone number and 2FA password when prompted.
