# Baziliksina Userbot 🌸

**Baziliksina** is an autonomous, highly modular AI-driven Telegram companion (Userbot) built on top of the MTProto-client **Telethon**. Powered by **Google Gemini API** models for deep reasoning and unified function calling, alongside the **Pollinations.ai** gateway for generative media synthesis.

---

## Architectural Layout 📂

The project is organized into structured, highly cohesive directories:

```
baziliksina/
├── main.py                     # Primary Launcher (configures sys.path)
├── .env                        # Active Environment Configurations (API Keys, limits, proxies)
├── .env.example                # Unified template for environment variables
├── .gitignore                  # Git tracking exclusion filters
│
├── config/
│   ├── config.py               # Centralized dynamic configuration proxy & DSL parser
│   ├── system_prompt.txt       # Technical VM & sandbox instruction prompt template
│   ├── character.txt           # Personality, cynical tone, and lazy style prompt template
│   ├── rules_prompt.txt        # Behavioral and Telegram HTML formatting rules
│   ├── env_prompt.txt          # Active environment chat parameters template
│   └── summarize_prompt.txt    # Instructions for context compressing
│
├── database/
│   └── db_manager.py           # Asynchronous SQLite DB Manager with settings override table
│
├── core/
│   ├── bot.py                  # Direct MTProto client, and unified network event router
│   ├── gemini_manager.py       # Orchestrates dialogue turns and coordinates modules
│   ├── context_manager.py      # Dual-engine context manager (Text & File strategies)
│   ├── permission_manager.py   # User ranks & granular AI CRUD+INVOKE permission matrix
│   ├── service_manager.py      # Unified background services & recurring cron jobs registry
│   ├── command_manager.py      # CLI command parser, task canceler & pipeline execution engine
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
│   ├── utils.py                # Safe JSON serializers and custom Markdown-to-HTML parser
│   ├── parser.py               # Dissects raw MTProto structures (premium emojis & gifts)
│   ├── downloader.py           # Media downloader and transcoding interface (FFmpeg)
│   └── proxy_manager.py        # Modular segregated proxy pools rotation (PySocks)
│
└── tools/                      # MODULAR EXTENSION SYSTEMS TOOLKIT
    ├── __init__.py             # Package routing & unified dynamic re-exports
    ├── system_tools.py         # Sandbox VM, raw SQL queries, shell commands & Tor controller
    ├── file_tools.py           # Disk I/O, streaming downloads (yt-dlp) & message forwarding
    ├── web_tools.py            # Search engine, scraper & custom HTTP request tool
    ├── telegram_tools.py       # MTProto client actions, polling, muting, kicking, rich messages
    ├── scheduler_tools.py      # Persistent Scheduled tasks, SQLite timers & triggers
    ├── media_tools.py          # Generative image, audio & video Pollinations gateway
    ├── site_tools.py           # Sandboxed dynamic site hosting & DevOps testing
    ├── command_tools.py        # Custom CLI commands & user ranks management
    └── service_tools.py        # Background services & recurring cron jobs management
```

---

## Core Features 🌟

### 1. CLI Commands System & Pipeline Execution Engine
* **POSIX-Style CLI Parser:** Supports POSIX short/long flags (`--no-save`, `-n`), quotes, positional arguments, and media caption commands.
* **Pipeline Operators:** Chain tools and commands seamlessly using `;` (sequence), `&&` (success condition), `||` (fallback), and `|` (pipe stdout to next input).
* **Instant Generation Control:** `/send` and `/stop` commands feature instant active task cancellation (`task.cancel()`) with optional uncommitted output purging.
* **Contextual Prompt Editor (`/prompt`):** Edit system prompts dynamically via regex search, replacement, and contextual anchor insertions.

### 2. Multi-Tier User Ranks & Granular AI Permissions
* **Rank Hierarchy:** `100` (ROOT_ADMIN), `80` (ADMIN), `50` (PRIORITY), `10` (USER), `0` (BLOCKED).
* **Immutable Root Admins:** `ADMINS` config mapping protects root creator accounts from unauthorized demotion.
* **Granular CRUD+INVOKE Matrix:** Configurable AI permissions across `COMMANDS`, `TOOLS`, `TAGS`, `SERVICES`, `CRON`, and `SITES` for `CREATE`, `EDIT`, `DELETE`, `VIEW_INFO`, `VIEW_CONTENT`, `LIST`, and `INVOKE`.

### 3. Unified Background Services & Cron Jobs
* **Services Engine:** Orchestrates long-running background tasks (`keep_alive`, `connection_monitor`, `timers_loop`, `web_server`, plus custom AI services).
* **Cron Engine:** Schedules recurring background jobs driven by interval specs or custom DSL expressions.

### 4. Dual-Engine Context & Token Strategies
* **Text Strategies:** `summarize` (AI summarization), `trim` (drop oldest $N$ turns), `hybrid`, and `none`.
* **File Strategies:** `trim`, `summarize` (generates lightweight AI media text summaries), `hybrid`, and `none`.
* **Explicit File Inspection (`AUTO_ATTACH_FILES_TO_CONTEXT=False`):** Prevents context bloat by logging media as text metadata references. AI inspects files via explicit tool calls.
* **Trailing Turn Guard:** Prevents Gemini API `400 INVALID_ARGUMENT` errors by ensuring history payloads always terminate with a `user` turn.

### 5. Rich Messages & Telegram HTML Formatting
* **Rich Messages (`send_rich_message` / `<rich_message>`):** Compose multi-block articles with inline text, photos, videos, collages, interactive maps, and collapsible details.
* **Strict Telegram HTML Engine:** Full support for `<b>`, `<i>`, `<u>`, `<s>`, `<tg-spoiler>`, `<tg-emoji>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>`, `<details>`, `<sub>`, `<sup>`, and `<mark>`.

### 6. Dynamic Key Quota Rotation (Dynamic Cooldowns)
* **Optimal cooldown tracking:** Automatically parses Google JSON error response and sets cooldown to `retryDelay`.
* **Pacific Midnight reset:** Dynamically calculates remaining seconds to Midnight in California (US/Pacific).
* **Multi-tier Pollinations limits:** Proxy and SOCKS5 IP rotation via local Tor (NEWNYM signals).

### 7. Isolated Sandbox Virtual Machine (Sandbox VM)
* **File Isolation:** Bound Telethon proxies and downloader structures are jailed to the `bot_workspace` relative path.
* **Secrets masking:** Reading configuration files, environment variables, or tokens returns `[REDACTED_SECURITY_SENSITIVE_DATA]`.

### 8. Isolated Sandboxed Dynamic Site Hosting (AI DevOps) 🌐
* **Dynamic Micro-Websites:** Build, compile, and hot-update isolated Python-driven web applications on the fly.
* **DevOps Auto-Testing & Rollbacks:** Code is validated via dry-run test execution before deployment.

### 9. RESTful Web Server Administrative Panel 📊
* **Host auto-detection:** Dynamically resolves network interface IP address and binds to it.
* **IP Whitelisting & ACL Middleware:** Validates incoming connections on network socket level.

---

## CLI Command Reference Guide 🛠️

### User Commands
* `/q [--no-save / -n] [text]` — Send message without triggering AI response.
* `/stop [--purge / -p]` — Stop active AI generation in current chat.
* `/send [--drop-previous / -d] [text]` — Instant query to AI, resetting previous task.
* `/help [all/user/admin/command/category]` — Display help catalog.

### Admin Commands (Rank 80+)
* `/admin [set/reset/info] [user_id/@username] [rank] [perms_json]` — Manage user ranks & permissions.
* `/config [get/set/list] [key] [value]` — Inspect or update configuration.
* `/prompt [filename] [replace/insert_after/insert_before/delete] [pattern] [text]` — Edit prompt files.
* `/shell [command]` — Execute bash/shell command in sandbox.
* `/telegram [method] [args_json]` — Execute Telethon or raw TL action.
* `/run [code]` — Execute Python script in Sandbox VM.
* `/sql [query]` — Execute raw SQL query.
* `/request [method] [url] [json_data]` — Send HTTP request.
* `/log [get/set] [lines/category/level]` — Read or adjust log settings.
* `/command`, `/tool`, `/tag`, `/service`, `/cron`, `/timer`, `/trigger` — Element managers.

---

## Dynamic Sites Management 🌐

### REST API Endpoints (Bearer Authorized)
* `GET /api/sites` — List all registered dynamic websites, statuses, and disk usage.
* `POST /api/sites/add` — Create or update a dynamic site (payload requires `name`, `config`, `modules`).
* `GET /api/sites/details/{name}` — Retrieve precise configuration and module code of a specific site.
* `GET /api/sites/logs/{name}` — Read or stream console prints and tracebacks for troubleshooting.
* `POST /api/sites/command/{name}` — Execute a shell command inside the site's isolated subdirectory.
* `DELETE /api/sites/delete/{name}` — Remove database records and permanently wipe the site's folder.

---

## Installation & Launch 🚀

### 1. Install Requirements
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in credentials.

### 3. Launching
```bash
python main.py
```
```
