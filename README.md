# Baziliksina Userbot 🌸

**Baziliksina** is an autonomous, highly modular AI-driven Telegram companion (Userbot) built on top of the MTProto-client **Telethon** [README]. The bot is natively powered by **Google Gemini API** models for deep reasoning and unified function calling, alongside the **Pollinations.ai** gateway for generative image, audio, and video synthesis [README].

Designed to function as a natural, self-sustaining Telegram user, Baziliksina processes multimodal context (images, videos, documents, voice notes, and video notes), manages persistent databases, schedules long-term timers, configures reactive triggers, and dynamically compiles and registers new custom tools at runtime [README].

---

## Logical Folder Structure 📂

The project is organized into logical packages [README]:

```
baziliksina/
├── main.py                     # Primary Launcher (configures sys.path and boots core/bot.py)
├── .env.example                # Unified template for environment variables
├── .gitignore                  # Git tracking exclusion filters
│
├── config/
│   └── config.py               # Centralized configuration validator, default paths, and env loading
│
├── database/
│   └── db_manager.py           # Asynchronous SQLite DB Manager (aiosqlite) with 10 tables
│
├── core/
│   ├── bot.py                  # Direct MTProto client, network listener, and event router
│   ├── gemini_manager.py       # Orchestrates dialogue turns, token limits, and segment actions
│   ├── key_manager.py          # API Quotas, Model, and Key Rotation Manager
│   ├── sandbox.py              # Isolated secure virtual execution sandbox for python VM
│   └── registry.py             # Active RAM Function Registry of system and custom tools
│
├── services/
│   └── services.py             # Implements missed messages synchronization and status keep-alive
│
├── utils/
│   ├── utils.py                # Safe JSON serializers and custom HTML parser
│   ├── parser.py               # Dissects raw MTProto structures (resolves rich styles & quotes)
│   ├── downloader.py           # Media downloader and transcoding interface (FFmpeg)
│   └── proxy_manager.py        # Modular segregated proxy pools rotation (PySocks)
│
└── tools/
    └── tools.py                # Unified root system toolset containing 39+ functions
```

---

## Formatting Capabilities 📝

Baziliksina features native support for advanced Telegram formatting entities:
*   **Expandable / Collapsible Blockquotes:** `<blockquote expandable>Collapsible text inside</blockquote>`
*   **Subscript and Superscript:** `<sub>sub</sub>` and `<sup>sup</sup>`
*   **Marked Text (Highlighter):** `<mark>highlighted text</mark>`
*   **Time Tags:** `<time datetime="2026-06-22T06:54:00Z">June 2026</time>`

### Rich Parser & Safe HTML Formatter
The userbot parses formatting on incoming messages (caching them as structured metadata inside SQLite) and uses a **Safe HTML escaping parser** (`safe_telegram_html` inside `utils.py`) to prevent Telegram API errors by escaping raw naked characters like `<`, `>`, and `&` while preserving allowed formatting tags [23, 25].

---

## XML-Style Execution Blocks & Labels 🏷️

Rather than sending a single plain text blob, the AI can structure its output into XML-style block containers and individual action labels to perform compound operations sequentially, in parallel, or in the background:

### Blocks
*   `<seq> ... </seq>`: Executes contained segments sequentially (default behavior) [33, 34].
*   `<par> ... </par>`: Executes contained segments in parallel using `asyncio.gather` [33, 34].
*   `<bg> ... </bg>`: Schedules segments to run in the background (using `asyncio.create_task`), allowing the AI to complete its turn immediately without waiting [33, 34].

### Segment Labels
The AI can output the following labels within blocks, which the executor parses and executes:
1.  **Direct Replies:** `[Reply: MSG_ID] Conversational text here` (routes the reply arrow directly to the target message) [33, 34].
2.  **Reactions:** `[React: MSG_ID | emoji_or_document_id]` (sets/clears message reactions natively; use `none` to remove) [33, 34].
3.  **Media Album Attachments:** `[Attach: photo.jpg, video.mp4 | Caption]` (delivers media groups as high-quality cohesive albums) [33, 34].
4.  **Edit Message:** `[Edit: MSG_ID | New text]` (updates existing own messages) [33, 34].
5.  **Delete Message:** `[Delete: MSG_ID]` (deletes own or other messages if admin permissions are held) [33, 34].
6.  **No-Op Ignore:** `[NoOp: reason | continue=True/False]` (completes the turn without replying; setting `continue=True` allows the generation loop to continue) [33, 34].
7.  **Direct Tool Execution:** `[Tool: tool_name | param1=val1, param2=val2]` (triggers any system or custom tool natively) [33, 34].

### Shielding & Escaping
If the AI wants to output a label or block as literal readable text to the chat (rather than executing it), it can shield it using backslashes:
`\[Reply: 12345\]` -> Delivered to the chat cleanly as `[Reply: 12345]`.

---

## Enhanced System Tools (New Additions) 🛠️

Several powerful tools have been added to the root system registry:
1.  `send_media_message`: Sends single or multiple files (album) with custom blurs, self-destruct timers (`ttl`), or spoiler tags [37].
2.  `edit_message`: Modifies previously sent own messages [37].
3.  `delete_message`: Removes messages from the chat with administrative clearance checks [37].
4.  `update_avatar`: Changes the userbot's or target chat's profile picture dynamically [37].
5.  `send_poll`: Sends native Telegram polls or trivia quizzes with custom options, anonymous/public voters, multiple choice, correct answers, and wrong-answer explanations [37].
6.  `send_http_request`: A generic web action requester allowing the AI to call external REST APIs using custom payloads, headers, or query parameters [37].

---

## FloodWait & Caching System 🗄️

To prevent hitting `GetFullUserRequest` flood waits during missed messages synchronization (`catch_up_missed_messages`), the bot integrates a database-level caching mechanism [25, 31, 35]:
*   When a profile's metadata is retrieved, it is saved in SQLite alongside an active `timestamp` updated via `CURRENT_TIMESTAMP` on conflict updates [31].
*   Before calling Telethon API methods for user/chat metadata, the parser checks if the database entry is newer than `PROFILE_UPDATE_INTERVAL` (1 hour) [25]. If the cache is still fresh, all Telegram API calls and avatar downloads are skipped [25].

---

## Generation & Flow Control Triggers

Configuration parameters to manage flow triggers:
*   `BOOTSTRAP_TRIGGER_GENERATION` (Default: `true`): Whether the AI automatically generates replies for the latest active conversations right after the first database cold bootstrap completes [13, 35].
*   `CATCH_UP_TRIGGER_GENERATION` (Default: `true`): Whether the AI automatically generates replies for any unaddressed incoming messages synced during network drops or inactivity catch-up [13, 35].
*   `USE_SYSTEM_PROMPT` (Default: `true`): Controls whether the comprehensive technical instructions and profile logs are injected into the Gemini API config block [13, 34].

---

## Unified Profile Logs (System Prompt) 👤

The system instructions prompt dynamically extracts and formats Premium profile data for the active Assistant account and the Creator/Owner account:
*   **Assistant Profile:** Numerical ID, first/last name, username, phone, premium/verified/scam/fake flags, restricted state, birthday, bio.
*   **Creator Profile:** Creator numerical ID (`OWNER_ID`), first/last name, creator username, premium/verified/scam/fake flags, restricted state, birthday, bio.

The core prompt containing the technical guides and XML rules is formatted first, while the custom style prompt (`character.txt`) is appended **at the very end** to maximize Gemini's focus on conversational traits [34].

---

## Installation & Launch 🚀

### 1. Install System Dependencies
Ensure **Python 3.10+**, **FFmpeg**, and **Tor** are active:

*   **Linux (Debian/Ubuntu):**
    ```bash
    sudo apt update && sudo apt upgrade -y
    sudo apt install python3 python3-pip ffmpeg tor git -y
    ```

### 2. Install Project Requirements
```bash
git clone https://github.com/ProgVM/baziliksina.git
cd baziliksina
pip install -r requirements.txt
```

### 3. Launching
Run the primary launcher script:
```bash
python main.py
```
