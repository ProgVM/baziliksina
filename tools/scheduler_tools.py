# tools/scheduler_tools.py
import asyncio
import logging
import time
from typing import List, Any

import config
import tools

logger = logging.getLogger("Tools.Scheduler")

class AIToolKitScheduler:
    def set_task_timer(self, delay_seconds: int, action_description: str, code_to_execute: str = None, **kwargs) -> str:
        """Schedules a task or asynchronous Python code execution by timer."""
        if not tools.db:
            return "Error: Database is not initialized."
        cid = tools.current_chat_id.get()
        asyncio.create_task(tools.db.add_timer(cid, delay_seconds, action_description, code_to_execute))
        return f"Timer successfully set for {delay_seconds} seconds and saved to the DB."

    async def delete_task_timer(self, timer_id: int, **kwargs) -> str:
        """Deletes a scheduled timer from the database by its unique ID."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            await tools.db.delete_timer(timer_id)
            return f"Success. Timer ID {timer_id} cancelled."
        except Exception as e:
            return f"Error deleting timer: {str(e)}"

    async def list_task_timers(self, **kwargs) -> str:
        """Returns a formatted list of all scheduled timers."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            timers = await tools.db.get_pending_timers()
            if not timers:
                return "No scheduled timers."
            
            lines = []
            now = int(time.time())
            for t_id, chat_id, execute_at, action, code in timers:
                remaining = execute_at - now
                code_ref = "yes" if code else "no"
                lines.append(f"ID {t_id} | Chat {chat_id} | Will trigger in {remaining} sec | Task: '{action}' | Auto-code: {code_ref}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"

    async def set_wake_trigger(self, trigger_type: str, trigger_value: str, action_description: str, code_to_execute: str = None, **kwargs) -> str:
        """Sets an automatic wake trigger in the current chat."""
        if not tools.db:
            return "Error: Database is not initialized."
        cid = tools.current_chat_id.get()
        try:
            await tools.db.add_trigger(cid, trigger_type, trigger_value, action_description, code_to_execute)
            return f"Trigger '{trigger_type}' with value '{trigger_value}' successfully set."
        except Exception as e:
            return f"Error saving trigger: {str(e)}"

    async def delete_wake_trigger(self, trigger_id: int, **kwargs) -> str:
        """Deletes an active wake trigger from the SQLite database by its ID."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            await tools.db.delete_trigger(trigger_id)
            return f"Success. Trigger ID {trigger_id} deleted."
        except Exception as e:
            return f"Error deleting trigger: {str(e)}"

    async def list_task_triggers(self, **kwargs) -> str:
        """Returns a formatted text list of all active wake triggers for the current chat."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            cid = tools.current_chat_id.get()
            triggers = await tools.db.get_active_triggers(cid)
            if not triggers:
                return "There are no active wake triggers for this chat."
            
            lines = []
            for t_id, t_type, t_val, t_action, t_code in triggers:
                code_ref = "yes" if t_code else "no"
                lines.append(f"ID {t_id} | Type: '{t_type}' | Value: '{t_val}' | Task: '{t_action}' | Auto-code: {code_ref}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"

# Export methods to module level
toolkit_sched = AIToolKitScheduler()
for attr in dir(toolkit_sched):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_sched, attr)
