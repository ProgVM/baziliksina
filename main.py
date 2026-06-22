# main.py
import sys
import asyncio
from pathlib import Path

# Add the project root and all logical subdirectories to sys.path
# to ensure correct imports across packages and inside the VM sandbox
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

for sub in ["config", "core", "database", "services", "utils", "tools"]:
    sub_path = str(_root / sub)
    if sub_path not in sys.path:
        sys.path.append(sub_path)

# Import the core bot execution routine
from core.bot import main as bot_main

if __name__ == "__main__":
    try:
        asyncio.run(bot_main())
    except KeyboardInterrupt:
        print("Bot stopped by user via KeyboardInterrupt.")
        sys.exit(0)
