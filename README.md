# Baziliksina Userbot 🌸

**Baziliksina** is an autonomous, highly modular AI-driven Telegram companion (Userbot) built on top of the MTProto-client **Telethon** [README]. The bot is natively powered by **Google Gemini API** models for reasoning and unified function calling, alongside the **Pollinations.ai** gateway for generative image, audio, and video synthesis [README].

Designed to function as a natural, self-sustaining mobile or PC Telegram user, Baziliksina processes multimodal context (images, videos, documents, voice notes, and video notes), manages persistent databases, schedules long-term timers, configures reactive triggers, and dynamically compiles and registers new custom tools at runtime [README].

---

## Key Core Features 🌟

1. **Turn-Based Multimodal Memory:** Keeps absolute, context-aware records of chat history, automatically extracting local thread context alongside global cross-cutting events [33, 34].
2. **Segmented Action Blocks:** Structures conversational replies and systemic actions natively within XML block containers (`<seq>`, `<par>`, `<bg>`), enabling sequential, parallel, or background executions [33, 34].
3. **2026 Telegram Formatting Support:** Fully parses, serializes, and delivers rich message styles including expandable blockquotes, subscript/superscript tags, and highlighted marked text with built-in protection against HTML rendering failures [23, 25].
4. **Autonomous Schedulers and Triggers:** Schedules robust database timers (`set_task_timer`) and reactive regex/keyword triggers (`set_wake_trigger`) that run background code or wake up the AI [37].
5. **Dynamic Runtime Compilation:** Compiles custom, secure Python tools on the fly (`create_or_update_custom_tool`), instantly registering them in active memory and persisting them across database reboots [37].
6. **Robust Sandboxed Virtual Machine:** Executes untrusted Python scripts inside a safe, isolated `AsyncSandbox` chroot-like environment, utilizing a proxied client to prevent modifications to vital system assets [21, 28].
7. **Key Pool Rotation & Recovery:** Automatic failover handling with specialized recovery cooldowns for Gemini (Flash vs Pro rate limits) and Pollinations (pool blocking on exhausted owner keys) [19, 30].
8. **Asynchronous Transcoding Engine:** Direct support for transparent WebM stickers/emojis (transcoded to H.264 MP4) and Opus OGG voice recordings (transcoded to MP3) using FFmpeg to ensure seamless compatibility with Google Gemini API [26].

---

## Architectural Layout 📂

The project is structured into logical packages to separate responsibilities:

```
baziliksina/
├── main.py                     # Primary Launcher (configures sys.path and boots core/bot.py)
├── .env.example                # Documented environmental variables template
├── .gitignore                  # Git tracking exclusion filters
│
├── config/
│   └── config.py               # Path resolutions, defaults validation, and env loading
│
├── database/
│   └── db_manager.py           # Asynchronous SQLite connector (aiosqlite) with 10 tables
│
├── core/
│   ├── bot.py                  # Direct MTProto client, network listener, and event router
│   ├── gemini_manager.py       # Orchestrates dialogue turns, token limits, and segment actions
│   ├── key_manager.py          # Recovers, rotates, and registers Gemini & Pollinations keys
│   ├── registry.py             # Active FunctionRegistry of systemic and compiled custom tools
│   └── sandbox.py              # Isolated secure virtual execution sandbox for python VM
│
├── services/
│   └── services.py             # Implements missed messages synchronization and status keep-alive
│
├── utils/
│   ├── utils.py                # Serializers, sanitizers, and safe HTML formatting parser
│   ├── parser.py               # Dissects raw MTProto structures (resolves rich styles & quotes)
│   ├── downloader.py           # Media downloader and transcoding interface (FFmpeg)
│   └── proxy_manager.py        # Segregated, modular PySocks proxy pool rotational controller
│
└── tools/
    └── tools.py                # Unified root system toolset containing 38+ functions
```

---

## Execution Blocks & Action Labels 🏷️

Instead of returning a single plain-text response, the AI structures its replies inside XML block containers. This allows complex operations (such as responding to a message, reacting, deleting, and running an internet search) to occur in a structured order.

### Wrapper Blocks
*   `<seq> ... </seq>`: Executes the enclosed action labels and text replies sequentially (default behavior).
*   `<par> ... </par>`: Executes the actions in parallel using `asyncio.gather` for immediate response times.
*   `<bg> ... </bg>`: Launches the actions in the background as an independent asyncio task, allowing the main dialogue loop to finish instantly without waiting.

### Action Labels
*   **Text Replies:** `[Reply: MSG_ID] Your text here` — Sends a targeted reply to the specified message ID using Telethon [33, 34].
*   **Reactions:** `[React: MSG_ID | emoji_or_document_id]` — Sets standard reactions or custom premium emojis. Setting `none` removes the reaction [33, 34].
*   **Media Album Attachments:** `[Attach: photo.jpg, video.mp4 | Caption]` — Delivers local workspace files as a cohesive media album [33, 34].
*   **Edit Message:** `[Edit: MSG_ID | New text]` — Edits an existing own message [33, 34].
*   **Delete Message:** `[Delete: MSG_ID]` — Deletes the specified message [33, 34].
*   **No-Op Ignore:** `[NoOp: reason | continue=True/False]` — Silent ignore. If `continue` is True, the AI keeps reasoning in the loop; if False, it terminates the turn [33, 34].
*   **Direct Tool Call:** `[Tool: tool_name | param1=val1, param2=val2]` — Standard call to any registered tool [33, 34].

### Shielding & Escaping
To prevent the parser from executing a block or label (for instance, when explaining usage to a user), the AI shields the brackets using backslashes:
`\[Reply: 12345\]` — Safely delivered as readable text in the chat, rendering as `[Reply: 12345]`.

---

## Comprehensive Database Schema 🗄️

The `DBManager` manages 10 dedicated SQLite tables to record the entire userbot lifecycle:

1.  `messages`: Dialogue history across all chats, storing role, text, raw Gemini Content JSONs, media references, and message IDs.
2.  `msgs_meta`: Secondary visual metadata (reactions, inline button layouts, premium emojis).
3.  `summaries`: Global summarized cross-chat history used to compress active context limits.
4.  `shared_memory`: Shared global memory (key-value) accessible across all scripts.
5.  `timers`: Scheduled task timers with optional code to execute when triggered.
6.  `triggers`: Reactive keyword/regex auto-wake rules.
7.  `users_meta`: Profiles of premium/standard users (verifications, scam flags, avatars, bios).
8.  `chats_meta`: Metadata of groups/channels (type, titles, linked channels, descriptions).
9.  `api_keys`: Database registry of all loaded Google Gemini and Pollinations keys, status, and quotas.
10. `custom_tools`: Complete source code and schemas of user-compiled custom dynamic tools.

---

## Unified System Toolset (38 Systemic Functions) 🛠️

Baziliksina's system registry provides the following core tools:

### Category 1: File System & Workspace
*   `save_file_to_workspace`: Saves text/hex binary content to a workspace file.
*   `save_file_from_telegram`: Downloads files directly from specified Telegram message IDs.
*   `read_file_from_workspace`: Reads text files or returns hex dumps of binary files.
*   `list_workspace_files`: Lists all files inside the workspace.
*   `delete_file_from_workspace`: Deletes files from the workspace disk.
*   `download_content_from_url`: Downloads static files or utilizes `yt-dlp` for streaming audio/video.

### Category 2: Web Search & Scraping
*   `internet_search`: DuckDuckGo text search engine for real-time information retrieval.
*   `internet_media_search`: Multimedia/PDF file search.
*   `scrape_url`: Text extraction from web pages (strips script/CSS and decomposition tags).
*   `send_http_request`: Direct REST API request executor supporting custom payloads, headers, and params.

### Category 3: Telegram Automation
*   `send_agent_message`: Relays replies, cross-chat messages, and markdown blockquotes for deleted messages.
*   `execute_telegram_action`: Automated execution of raw MTProto calls or Telethon methods.
*   `send_inline_bot_result`: Direct clicking and relaying of inline bot commands (`@gif`, `@pic`).
*   `click_inline_button`: Automated interaction with inline keyboard layouts of other bots.
*   `set_message_reaction`: Natively set/remove message reactions.
*   `send_telegram_media`: Sends Telegram files by utilizing raw document IDs and access hashes.
*   `send_media_message`: Sends single or multiple files (album) with custom blurs, self-destruct timers (`ttl`), or spoiler tags.
*   `edit_message`: Modifies previously sent own messages.
*   `delete_message`: Administrative deletion of own or other messages.
*   `update_avatar`: Changes the userbot's or target chat's profile picture.

### Category 4 & 5: Timers & Triggers
*   `set_task_timer` / `delete_task_timer` / `list_task_timers`: Database task timers manager.
*   `set_wake_trigger` / `delete_wake_trigger` / `list_task_triggers`: Reactive keyword auto-wake rules manager.

### Category 6: Multimedia Generative AI
*   `generate_image`: Flux Schnell, anime-zimage, and Grok image generation with size configuration.
*   `generate_audio`: TTS and music synthesis utilizing Qwen and ElevenLabs models.
*   `generate_video`: Physics-aware short video synthesis using Alibaba Wan and LTX.
*   `upload_file_to_public_host`: Uploads local files to secure anonymous hosts (Telegraph, Uguu, Pollinations).

### Category 7: System Control & Database Integration
*   `no_op_ignore`: Completes generation turns without text output. Supports optional `continue_loop` parameters.
*   `run_sandboxed_command`: Protected execution of basic terminal bash shell commands.
*   `execute_python_code`: Sandbox execution VM for asynchronous scripts.
*   `upload_file_to_google`: Uploads large local assets to Gemini cloud storage.
*   `get_chat_history_from_db`: Extracts raw SQLite logs for specific chats.
*   `get_telegram_object_info`: Pulls user/channel descriptions, verifications, scam tags, and raw MTProto JSONs.
*   `get_telegram_message_details`: Pulls full message structures, reactions, views, forwards, and inline button layouts.
*   `execute_sql_query`: Raw SELECT/INSERT/UPDATE database execution engine.
*   `create_or_update_custom_tool` / `delete_custom_tool`: Dynamically registers/removes runtime tools.

---

## Configuration Variables (`.env`) ⚙️

Customize the `.env` template in your root directory before launching:

```ini
# --- TELEGRAM API CONFIGURATION ---
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_telegram_hash
TELEGRAM_SESSION_NAME=baziliksina
OWNER_ID=2113692455

# --- SQLITE ENGINE SETTINGS ---
DB_NAME=bot_context.db
SQLITE_JOURNAL_MODE=WAL

# --- GOOGLE GEMINI CONFIGURATION ---
GEMINI_API_KEYS=key1,key2,key3
GEMINI_MODELS=gemini-2.5-flash,gemini-2.5-pro

# --- FLOW & GENERATION TRIGGERS ---
BOOTSTRAP_TRIGGER_GENERATION=true
CATCH_UP_TRIGGER_GENERATION=true
USE_SYSTEM_PROMPT=true
```

---

## Installation & Setup 🚀

### 1. Install System Dependencies
Ensure **Python 3.10+**, **FFmpeg**, and **Tor** are active:

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

### 3. Launching
Run the primary launcher script:
```bash
python main.py
```
Enter your phone number and 2FA password (if enabled) on first run to authorize the Telethon session.
