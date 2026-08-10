# tools/command_tools.py
import os
import json
import logging
from typing import List, Dict, Any, Optional

import config
import tools
from permission_manager import permission_manager, RankLevel

logger = logging.getLogger("Tools.Commands")

class AIToolKitCommands:
    async def create_or_update_custom_command(self, name: str, code: str, help_text: str = None, category: str = "general", **kwargs) -> str:
        """
        Creates a new or updates an existing custom CLI command in the system.

        Args:
            name: Unique command name (without leading slash, e.g. 'mycmd').
            code: Full Python source code for the command handler.
            help_text: Short description displayed in /help.
            category: Command category ('general', 'utility', 'fun', 'admin').
        """
        if not permission_manager.can_ai_perform("COMMANDS", "CREATE") and not permission_manager.can_ai_perform("COMMANDS", "EDIT"):
            return "Error: Permission denied. AI is not authorized to create/edit commands."

        if not tools.db:
            return "Error: Database is not initialized."

        from utils import matches_filter
        if not matches_filter(code, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
            return "Security error: Command Python code contains terms blocked by security policy."

        try:
            await tools.db.save_custom_command(name, code, help_text=help_text, category=category)
            return f"Success! Custom command /{name.lstrip('/')} created/updated successfully."
        except Exception as e:
            return f"Error saving custom command: {str(e)}"

    async def delete_custom_command(self, name: str, **kwargs) -> str:
        """
        Deletes a custom CLI command from the database.

        Args:
            name: Command name to delete.
        """
        if not permission_manager.can_ai_perform("COMMANDS", "DELETE"):
            return "Error: Permission denied. AI is not authorized to delete commands."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            deleted = await tools.db.delete_custom_command(name)
            return f"Success. Command /{name.lstrip('/')} deleted." if deleted else f"Error: Command /{name} not found."
        except Exception as e:
            return f"Error deleting custom command: {str(e)}"

    async def list_custom_commands(self, **kwargs) -> str:
        """Returns a formatted list of all custom CLI commands registered in the database."""
        if not permission_manager.can_ai_perform("COMMANDS", "LIST"):
            return "Error: Permission denied. AI is not authorized to list commands."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            cmds = await tools.db.get_all_custom_commands()
            if not cmds:
                return "No custom commands found in the database."
            lines = [f"- /{c['name']} [{c['category']}]: {c['help_text'] or 'No description'}" for c in cmds]
            return f"=== Custom Commands ({len(cmds)}) ===\n" + "\n".join(lines)
        except Exception as e:
            return f"Error listing custom commands: {str(e)}"

    async def get_custom_command_details(self, name: str, **kwargs) -> str:
        """Retrieves metadata, help text, and Python code of a custom command."""
        if not permission_manager.can_ai_perform("COMMANDS", "VIEW_INFO") and not permission_manager.can_ai_perform("COMMANDS", "VIEW_CONTENT"):
            return "Error: Permission denied."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            cmd = await tools.db.get_custom_command(name)
            if not cmd:
                return f"Error: Custom command /{name.lstrip('/')} not found."
            
            res = [
                f"Command Details for /{cmd['name']}:",
                f"- Category: {cmd['category']}",
                f"- Help Text: {cmd['help_text'] or 'None'}"
            ]
            if permission_manager.can_ai_perform("COMMANDS", "VIEW_CONTENT"):
                res.append(f"- Python Code:\n```python\n{cmd['code']}\n```")
            return "\n".join(res)
        except Exception as e:
            return f"Error retrieving command details: {str(e)}"

    async def manage_user_rank(self, target_user: str, rank: int, permissions: List[str] = None, **kwargs) -> str:
        """
        Sets or updates the numerical rank and explicit permissions list for a user.

        Args:
            target_user: Numerical Telegram ID or @username.
            rank: Target rank integer (100=Root, 80=Admin, 50=Priority, 10=User, 0=Blocked).
            permissions: Optional list of explicit permission strings (e.g. ['all'] or ['commands', 'tools']).
        """
        if not permission_manager.can_ai_perform("COMMANDS", "EDIT") and not permission_manager.can_ai_perform("COMMANDS", "CREATE"):
            return "Error: Permission denied. AI cannot manage user ranks."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            await tools.db.save_user_rank(target_user, rank, permissions)
            return f"Success! Rank for '{target_user}' set to {rank}."
        except Exception as e:
            return f"Error setting user rank: {str(e)}"


toolkit_commands = AIToolKitCommands()
for attr in dir(toolkit_commands):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_commands, attr)
