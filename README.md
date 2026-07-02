# Baziliksina Userbot 🌸

**Baziliksina** is an autonomous, highly modular AI-driven Telegram companion (Userbot) built on top of the MTProto-client **Telethon**. The bot is natively powered by **Google Gemini API** models for deep reasoning and unified function calling, alongside the **Pollinations.ai** gateway for generative image, audio, and video synthesis.

---

## Decoupled Folder Structure 📂

The project is organized into structured, highly cohesive directories:

```
baziliksina/
├── main.py                     # Primary Launcher (configures sys.path)
├── .env.example                # Unified template for environment variables
├── .gitignore                  # Git tracking exclusion filters
│
├── config/
│   ├── config.py               # Centralized configuration validator, and multi-tier loader
│   ├── system_prompt.txt       # Technical VM & sandbox instruction prompt template
│   ├── character.txt           # Personality, cynical tone, and lazy style prompt template
│   ├── rules_prompt.txt        # Behavioral rules inside group chats template
│   ├── env_prompt.txt          # Active environment chat parameters template
│   └── summarize_prompt.txt    # Instructions for context compressing
│
├── database/
│   └── db_manager.py           # Asynchronous SQLite DB Manager with settings override table
│
├── core/
│   ├── bot.py                  # Direct MTProto client, and unified network event router
│   ├── gemini_manager.py       # Orchestrates dialogue turns and coordinates modules
│   ├── context_manager.py      # Computes token limits and manages context logs
│   ├── prompt_interpolator.py  # Dynamically loads and interpolates config templates
│   ├── response_executor.py    # Parsed sequential/parallel/background XML segment actions
│   ├── key_manager.py          # API Quotas, Model, and Key Rotation Manager
│   ├── sandbox.py              # Isolated secure virtual execution sandbox (reboot VM)
│   └── registry.py             # Active RAM Function Registry of system and custom tools
│
├── server/
│   └── server.py               # Asynchronous HTTP admin REST API with IP ACL middleware
│
├── services/
│   └── services.py             # Implements missed messages synchronization
│
├── utils/
│   ├── utils.py                # Safe JSON serializers and custom HTML parser
│   ├── parser.py               # Dissects raw MTProto structures (premium emojis & gifts)
│   ├── downloader.py           # Media downloader and transcoding interface (FFmpeg)
│   └── proxy_manager.py        # Modular segregated proxy pools rotation (PySocks)
│
└── tools/
    └── tools.py                # Unified root system toolset containing 50+ functions
```

---

## Secure Sandbox Virtual Machine 🛡️

Baziliksina implements an isolated, self-healing sandbox environment (`sandbox.py`) for the VM execution of arbitrary Python scripts:
*   **Dynamic Tool Binding:** Directly binds registered tools from the dynamic `FunctionRegistry` into the execution scope.
*   **Secrets Masking (`SandboxedConfig`):** Proxies the `config` module. Attempts to read sensitive tokens (like `API_HASH` or `GEMINI_API_KEYS`) return `[REDACTED_SECURITY_SENSITIVE_DATA]`.
*   **Self-Healing & Crash-Recovery:** Isolates execution blocks. In case of syntax failures, runtime errors, or memory overflows, the sandbox wipes the contaminated scope and fully restores the VM state for subsequent turns.

---

## Secure REST Administration Server 🖥️

An integrated asynchronous HTTP server is built directly on top of `aiohttp.web` inside `server/server.py`:
*   **IP Whitelist ACL Middleware:** Automatically verifies client host IP against `config.WEB_SERVER_IP_ACL` on network level.
*   **API Tokens Authorization:** Validates `Authorization: Bearer` keys with granular permissions and rate limits.
*   **Administrative Endpoints:** 24 control points to dynamically alter `config.py` in RAM, inspect database queries, download DB backups, read real-time log files, and initiate hot restarts.

---

## Formatting Capabilities 📝

Baziliksina features native support for advanced Telegram formatting entities:
*   **Expandable / Collapsible Blockquotes:** `<blockquote expandable>Collapsible text inside</blockquote>`
*   **Subscript and Superscript:** `<sub>sub</sub>` and `<sup>sup</sup>`
*   **Marked Text (Highlighter):** `<mark>highlighted text</mark>`
*   **Time Tags:** `<time datetime="2026-06-22T06:54:00Z">June 2026</time>`

---

## XML-Style Execution Blocks & Labels 🏷️

Rather than sending a single plain text blob, the AI can structure its output into XML-style block containers and individual action labels to perform compound operations sequentially, in parallel, or in the background:

### Blocks
*   `<seq> ... </seq>`: Executes contained segments sequentially.
*   `<par> ... </par>`: Executes contained segments in parallel using `asyncio.gather`.
*   `<bg> ... </bg>`: Schedules segments to run in the background, allowing the AI to complete its turn immediately without waiting.

### Segment Labels / XML Tags
The AI can output the following labels/tags within blocks, which the executor parses and executes:
1.  **Direct Replies:** `<reply id="MSG_ID">Conversational text here</reply>`
2.  **Reactions:** `<react id="MSG_ID" emoji="emoji_or_document_id" />`
3.  **Media Album Attachments:** `<attach files="photo.jpg, video.mp4" caption="Caption" />`
4.  **Edit Message:** `<edit id="MSG_ID">New text</edit>`
5.  **Delete Message:** `<delete id="MSG_ID" />`
6.  **No-Op Ignore:** `<noop reason="reason" continue="True/False" />`
7.  **Direct Tool Execution:** `<tool name="tool_name" param1="val1" />`

---

## Installation & Launch 🚀

Ensure **Python 3.10+**, **FFmpeg**, and **Tor** are active on the host machine.

### 1. Install Project Requirements
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in API credentials.

### 3. Launching
Run the primary launcher script:
```bash
python main.py
```
