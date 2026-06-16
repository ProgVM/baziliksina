# Baziliksina Userbot

Baziliksina is a modular, secure, and state-of-the-art Telegram Userbot designed on top of the **Telethon** MTProto library and powered natively by **Google Gemini** models. 

Equipped with a secure **Sandboxed Python Virtual Machine (VM)**, dynamic **Function Registry**, and robust multi-level key and model rotation, Baziliksina acts as an autonomous AI agent capable of executing complex system tasks, managing Telegram chats, generating generative multimedia, and healing its own runtime execution.

---

## Key Features

- **Dynamic Function Registry & Class-Based Tools**: Avoids hardcoding. Custom or system-level tools are registered in a unified `FunctionRegistry` class on the fly.
- **Sandboxed Python VM (Sandbox)**: A fully isolated executing environment allowing the AI to write and execute asynchronous Python scripts securely.
- **Robust Model & Key Rotation**: Automatically monitors API rate limits (429) or server errors (503), dynamically rotating both keys and models with distinct recovery cooldowns (e.g., 5-hour cooldown for Flash, 24-hour for Pro models).
- **Automated DB Bootstrapping & Catch-Up**: On cold startup or network reconnection, the bot automatically syncs missed messages and populates its memory, preventing silent gaps.
- **Multimodal Audio & Video Transcoding**: Automatically converts unsupported formats on the fly (such as transparent VP9 `.webm` Telegram stickers to standard `.mp4`, and Opus `.ogg` voice notes to standard `.mp3` via FFmpeg).
- **Inline Bot Queries & Custom Tools**: The AI can make native inline calls to Telegram bots (like `@gif`, `@pic`, `@vote`) and create, update, or remove its own custom tools at runtime.
- **Mathematically Safe Debouncing**: An incremental counter-based debounce algorithm prevents double-sending or parallel generations on rapid burst messages.
- **Context-Aware Google File Uploads**: Programmatic uploading via `upload_file_to_google` instantly attaches file references (`Part.from_uri`) to the multi-turn context without clunky text strings.

---

## Project Structure (12 Core Modules)

- `bot.py` — The core event listener, managing Telegram network updates, reactions, and the debounce loop.
- `config.py` — Centralized configuration, managing all limits, SOCKS/Tor ports, and default parameters.
- `db_manager.py` — Asynchronous SQLite WAL-based database manager.
- `downloader.py` — High-precision media downloader and FFmpeg transcoder.
- `gemini_manager.py` — Core AI inference orchestrator, handling model calls, context limits, and system prompts.
- `key_manager.py` — API managers controlling Gemini and Pollinations rotation.
- `parser.py` — Rich parser extracting message payloads, emojis, reactions, and replies.
- `registry.py` — Centralized catalog storing metadata and callables of system and custom tools.
- `sandbox.py` — Safe execution virtual machine hosting the Sandboxed Telethon client.
- `services.py` — Background loops for keep-alive, network monitoring, and startup bootstrapping.
- `tools.py` — The class-based `AIToolKit` exposing 31 advanced system tools to the model.
- `utils.py` — General-purpose JSON encoders, deserializers, and path sanitizers.

---

## Installation & Setup

### Prerequisites

Ensure you have **FFmpeg** and **Tor** installed on your system.

**Termux (Android):**
```bash
pkg install python ffmpeg tor git
termux-setup-storage
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update
sudo apt install python3 python3-pip ffmpeg tor git
```

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/baziliksina.git
   cd baziliksina
   ```
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the environment template and edit your configurations:
   ```bash
   cp env.example .env
   # Edit .env with your favorite editor (e.g. nano .env)
   ```

4. Configure Tor ControlPort (Optional, required for IP-rotation):
   Ensure your `torrc` file has the following lines enabled:
   ```text
   SocksPort 9050
   ControlPort 9051
   CookieAuthentication 0
   ```

5. Start the bot:
   ```bash
   python bot.py
   ```

---

## License

This project is open-sourced under the MIT License.
