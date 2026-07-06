# Baziliksina Userbot 🌸

**Baziliksina** is an autonomous, highly modular AI-driven Telegram companion (Userbot) built on top of the MTProto-client **Telethon**. The bot is natively powered by **Google Gemini API** models for deep reasoning and unified function calling, alongside the **Pollinations.ai** gateway for generative image, audio, and video synthesis.

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
└── tools/                      # MODULAR EXTENSION SYSTEMS TOOLKIT
    ├── __init__.py             # Package routing & unified dynamic re-exports
    ├── system_tools.py         # Sandbox VM, raw SQL queries, shell commands & Tor controller
    ├── file_tools.py           # Disk I/O, streaming downloads (yt-dlp) & message forwarding
    ├── web_tools.py            # Search engine, scraper & custom HTTP request tool
    ├── telegram_tools.py       # MTProto client actions, polling, muting, kicking & bans
    ├── scheduler_tools.py      # Persistent Scheduled tasks, SQLite timers & triggers
    └── media_tools.py          # Generative image, audio & video Pollinations gateway
    ```

---

## Core Features 🌟

### 1. Dynamic Key Quota Rotation (Dynamic Cooldowns)
*   **Optimal cooldown tracking:** When encountering a `429 RESOURCE_EXHAUSTED` error, the key manager automatically parses the Google JSON error response and sets the cooldown period precisely to the returned `retryDelay` value (e.g. `12s`).
*   **Pacific Midnight reset:** Daily limits (Requests Per Day - RPD) take up to Pacific Midnight to reset. The manager dynamically calculates the remaining seconds to Midnight in California (US/Pacific) and schedules the key to wake up exactly at the turn of the day.
*   **Multi-tier Pollinations limits:** Seamless proxy and SOCKS5 IP rotation via local Tor (NEWNYM signals) for publishable keys (`pk_`), and ownership-based pool rotation for secret keys (`sk_`).

### 2. Multi-Chat Context Logging & Timestamps
*   **Temporal awareness:** Every incoming, outgoing, and system message stored in the SQLite context history is timestamped, allowing the model to perfectly track the timeline of the conversation.
*   **Rich formatting decoder:** Parses complex formatting elements (subscripts, superscripts, struck/underlined text, collapsible blockquotes, lists, links in poll options) straight into raw metadata blocks for complete situational awareness.
*   **Verification and Country codes:** Resolves full contact phone numbers, and maps phone prefixes to country region codes alongside any platform-specific restriction reasons.

### 3. Isolated Sandbox Virtual Machine (Sandbox VM)
*   **File Isolation:** Bound Telethon proxies and downloader structures are jailed to the `bot_workspace` relative path, strictly preventing path-traversal attacks.
*   **Secrets masking:** Any VM scripts trying to read configuration files, environment variables, or tokens return `[REDACTED_SECURITY_SENSITIVE_DATA]`.
*   **Self-healing scope:** Any syntax crashes or memory leaks during runtime code execution are safely isolated without affecting the userbot instance.

### 4. RESTful Web Server Administrative Panel
*   **Host auto-detection:** If no host is specified in the configurations, the web server dynamically resolves your network interface IP address and binds to it.
*   **IP Whitelisting & ACL Middleware:** Automatically validates incoming connections on network socket level.
*   **24 control points:** Fully authorizedREST endpoints to update RAM config parameters, fetch log files, execute raw SQL statements, and perform hot restarts.

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
