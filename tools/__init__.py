# tools/__init__.py
import os
import logging
from contextvars import ContextVar

logger = logging.getLogger("Tools")

# Global context variables and bot core references
current_chat_id: ContextVar[int] = ContextVar("current_chat_id")
current_reply_to_id: ContextVar[int] = ContextVar("current_reply_to_id")
client = None
db = None
key_manager = None
pollinations_key_manager = None
bot_callback_fn = None
ai_manager = None

# Universal redirect imports for full backward compatibility
from tools.system_tools import *
from tools.file_tools import *
from tools.web_tools import *
from tools.telegram_tools import *
from tools.scheduler_tools import *
from tools.media_tools import *
from tools.tag_block_tools import *

ROOT_TOOL_CATEGORIES = {
    "send_media_message": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "edit_message": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "delete_message": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "update_avatar": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_http_request": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "update_account_info": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "send_game_emoji": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "send_geolocation": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_premium_list": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_uncompressed_file": "Category 1: File System and Sandbox (Workspace File Management)",
    "send_audio_music": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "kick_user": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "mute_user": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "ban_user": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "unrestrict_user": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "click_keyboard_button": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "get_bot_commands": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_bot_command": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "forward_messages": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "save_file_to_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "save_file_from_telegram": "Category 1: File System and Sandbox (Workspace File Management)",
    "read_file_from_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "list_workspace_files": "Category 1: File System and Sandbox (Workspace File Management)",
    "delete_file_from_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "download_content_from_url": "Category 1: File System and Sandbox (Workspace File Management)",
    "internet_search": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "internet_media_search": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "scrape_url": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "send_agent_message": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "execute_telegram_action": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "click_inline_button": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_inline_bot_result": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "set_message_reaction": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_telegram_media": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_poll": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "get_group_members": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "edit_chat_participant_settings": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "get_chat_participant_info": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "join_telegram_chat": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "set_task_timer": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    "delete_task_timer": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    "list_task_timers": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    "set_wake_trigger": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    "delete_wake_trigger": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    "list_task_triggers": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    "generate_image": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "generate_audio": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "generate_video": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "upload_file_to_public_host": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "no_op_ignore": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "run_sandboxed_command": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "execute_python_code": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "upload_file_to_google": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_chat_history_from_db": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_telegram_object_info": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_telegram_message_details": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "execute_sql_query": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "create_or_update_custom_tool": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "delete_custom_tool": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)"
}

class ModularToolKit:
    def __getattr__(self, name):
        if name in globals():
            return globals()[name]
        raise AttributeError(f"'ModularToolKit' object has no attribute '{name}'")

toolkit = ModularToolKit()

def register_system_tools():
    """Automatically registers all root methods in the global FunctionRegistry."""
    from registry import registry
    for method_name, category in ROOT_TOOL_CATEGORIES.items():
        func = globals().get(method_name)
        if func:
            registry.register(
                name=method_name,
                callable_func=func,
                category=category,
                description=getattr(func, "__doc__", ""),
                is_custom=False
            )
    logger.info(f"Automatic registration completed. Successfully imported system tools: {len(ROOT_TOOL_CATEGORIES)}")
    
    # Register system tags and blocks
    from tools.tag_block_tools import register_system_tags_blocks
    register_system_tags_blocks()

# Export dynamically for top-level access
for attr_name in list(globals().keys()):
    if not attr_name.startswith("_") and attr_name != "register_system_tools" and attr_name != "ModularToolKit":
        globals()[attr_name] = globals()[attr_name]
