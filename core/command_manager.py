# core/command_manager.py
import os
import sys
import json
import re
import time
import shlex
import asyncio
import logging
import inspect
from typing import Dict, Any, Optional, List, Tuple, Union, Callable

import config
from permission_manager import permission_manager, RankLevel
from registry import registry, tag_block_registry, compile_custom_tool
from service_manager import service_manager
from utils import safe_serialize, matches_filter

logger = logging.getLogger("CommandManager")


class CLIArgs:
    """Parsed CLI arguments data structure."""
    def __init__(self, raw_cmd: str, command_name: str, positional: List[str], flags: Dict[str, Any], raw_tail: str):
        self.raw_cmd = raw_cmd
        self.command_name = command_name.lower().lstrip("/")
        self.positional = positional
        self.flags = flags
        self.raw_tail = raw_tail

    def has_flag(self, *flag_names: str) -> bool:
        """Checks if any of the given flag names exist in parsed flags."""
        for fn in flag_names:
            clean_fn = fn.lstrip("-").lower()
            if clean_fn in self.flags:
                return True
        return False

    def get_flag(self, flag_name: str, default: Any = None) -> Any:
        """Returns flag value or default."""
        clean_fn = flag_name.lstrip("-").lower()
        return self.flags.get(clean_fn, default)


def parse_cli_command(text: str) -> Optional[CLIArgs]:
    """
    Parses a command string into structured CLI arguments, flags, and tail parameters.
    Supports quotes, long flags (--flag, --key=val), short flags (-f, -abc), and positional args.
    """
    if not text or not text.strip().startswith("/"):
        return None

    text = text.strip()
    parts = text.split(maxsplit=1)
    cmd_name = parts[0][1:]
    
    if "@" in cmd_name:
        cmd_name = cmd_name.split("@")[0]

    tail = parts[1] if len(parts) > 1 else ""

    try:
        tokens = shlex.split(tail)
    except Exception:
        tokens = tail.split()

    positional = []
    flags = {}

    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.startswith("--"):
            if "=" in tok:
                k, v = tok[2:].split("=", 1)
                flags[k.lower()] = v
            else:
                k = tok[2:].lower()
                if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-"):
                    flags[k] = tokens[idx + 1]
                    idx += 1
                else:
                    flags[k] = True
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            short_flags = tok[1:]
            for char in short_flags:
                flags[char.lower()] = True
        else:
            positional.append(tok)
        idx += 1

    return CLIArgs(
        raw_cmd=text,
        command_name=cmd_name,
        positional=positional,
        flags=flags,
        raw_tail=tail
    )


class CommandManager:
    """
    Central Command Router managing built-in and dynamic custom commands,
    CLI parsing, pipeline execution operators, active generation cancellation,
    and granular permissions validation.
    """
    def __init__(self, db_manager=None, client_instance=None, ai_manager_instance=None):
        self.db = db_manager
        self.client = client_instance
        self.ai_manager = ai_manager_instance
        self._active_tasks: Dict[int, asyncio.Task] = {} # {chat_id: active_generation_task}
        self._handlers: Dict[str, Callable] = {}
        self._register_builtin_handlers()

    def bind_core_references(self, db_manager, client_instance, ai_manager_instance):
        """Binds core references required for execution context."""
        self.db = db_manager
        self.client = client_instance
        self.ai_manager = ai_manager_instance

    def register_generation_task(self, chat_id: int, task: asyncio.Task):
        """Tracks active AI generation task for cancellation via /stop or /send."""
        self._active_tasks[int(chat_id)] = task

    def unregister_generation_task(self, chat_id: int):
        """Unregisters active AI generation task upon completion."""
        self._active_tasks.pop(int(chat_id), None)

    async def cancel_generation(self, chat_id: int, purge: bool = False) -> bool:
        """Cancels active AI generation task for a chat and optionally purges uncommitted output."""
        cid = int(chat_id)
        task = self._active_tasks.get(cid)
        canceled = False
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            canceled = True

        self._active_tasks.pop(cid, None)

        if purge and self.db:
            try:
                async with self.db.db.execute(
                    "DELETE FROM messages WHERE chat_id = ? AND role = 'model' ORDER BY id DESC LIMIT 1",
                    (str(cid),)
                ) as cursor:
                    pass
                await self.db.db.commit()
            except Exception as e:
                logger.error(f"Error purging uncommitted message for chat {cid}: {str(e)}")

        return canceled

    def _register_builtin_handlers(self):
        """Registers all built-in command handlers."""
        self._handlers["q"] = self._cmd_q
        self._handlers["stop"] = self._cmd_stop
        self._handlers["zapoi"] = self._cmd_stop
        self._handlers["unzapoi"] = self._cmd_stop
        self._handlers["send"] = self._cmd_send
        self._handlers["admin"] = self._cmd_admin
        self._handlers["config"] = self._cmd_config
        self._handlers["prompt"] = self._cmd_prompt
        self._handlers["shell"] = self._cmd_shell
        self._handlers["telegram"] = self._cmd_telegram
        self._handlers["run"] = self._cmd_run
        self._handlers["sql"] = self._cmd_sql
        self._handlers["request"] = self._cmd_request
        self._handlers["log"] = self._cmd_log
        self._handlers["help"] = self._cmd_help
        self._handlers["message"] = self._cmd_message
        self._handlers["msg"] = self._cmd_message

        # Element Manager Commands
        self._handlers["command"] = self._cmd_element_command
        self._handlers["tool"] = self._cmd_element_tool
        self._handlers["tag"] = self._cmd_element_tag
        self._handlers["service"] = self._cmd_element_service
        self._handlers["cron"] = self._cmd_element_cron
        self._handlers["timer"] = self._cmd_element_timer
        self._handlers["trigger"] = self._cmd_element_trigger

    async def execute_pipeline(self, pipeline_text: str, user_id: int, chat_id: int, event=None) -> Optional[str]:
        """
        Executes a chain of commands or pipeline stages with operators (; , &&, ||, |).
        Pipes stdout/result from one stage as input/parameter to the next stage when '|' is used.
        Returns None for unrecognized commands to silently ignore external bot commands.
        """
        stages = re.split(r'(\s*(?:&&|\|\||;|\|)\s*)', pipeline_text)
        
        last_result = ""
        last_success = True

        idx = 0
        while idx < len(stages):
            stage_cmd = stages[idx].strip()
            if not stage_cmd:
                idx += 1
                continue

            if stage_cmd in [";", "&&", "||", "|"]:
                op = stage_cmd
                idx += 1
                if idx >= len(stages):
                    break
                next_cmd = stages[idx].strip()

                if op == "&&" and not last_success:
                    idx += 1
                    continue
                if op == "||" and last_success:
                    idx += 1
                    continue
                if op == "|":
                    next_cmd = f"{next_cmd} {last_result.strip()}"

                stage_cmd = next_cmd

            if stage_cmd.startswith("/"):
                res = await self.execute_single_command(stage_cmd, user_id, chat_id, event)
                if res is None:
                    return None
            else:
                res = stage_cmd

            last_result = str(res) if res is not None else ""
            last_success = not (last_result.startswith("Error:") or "Permission denied" in last_result)
            idx += 1

        return last_result if last_result else None

    async def execute_single_command(self, text: str, user_id: int, chat_id: int, event=None) -> Optional[str]:
        """
        Parses and executes a single command string.
        Returns None if command is unknown to silently ignore commands intended for other Telegram bots.
        """
        cli_args = parse_cli_command(text)
        if not cli_args:
            return None

        cmd_name = cli_args.command_name

        if cmd_name in self._handlers:
            handler = self._handlers[cmd_name]
            return await handler(cli_args, user_id, chat_id, event)

        if self.db:
            try:
                custom_cmd = await self.db.get_custom_command(cmd_name)
                if custom_cmd:
                    code = custom_cmd["code"]
                    compiled_func = compile_custom_tool(cmd_name, code)
                    if inspect.iscoroutinefunction(compiled_func):
                        res = await compiled_func(cli_args=cli_args, user_id=user_id, chat_id=chat_id, event=event)
                    else:
                        res = compiled_func(cli_args=cli_args, user_id=user_id, chat_id=chat_id, event=event)
                    return str(res) if res is not None else ""
            except Exception as e:
                return f"Error executing custom command /{cmd_name}: {str(e)}"

        # Silently ignore commands intended for other Telegram bots
        return None

    # --- USER COMMAND HANDLERS ---
    async def _cmd_q(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Quiet Send command (/q). Sends text without triggering AI generation."""
        no_save = args.has_flag("no-save", "n")
        msg_text = args.raw_tail

        for f in ["--no-save", "-n"]:
            msg_text = msg_text.replace(f, "").strip()

        if not msg_text:
            return "Usage: /q [--no-save / -n] [message text]"

        if self.db and not no_save:
            await self.db.save_message(str(chat_id), "user", msg_text, msg_id=getattr(event.message, "id", None) if event else None)

        return f"Quiet message recorded (No AI trigger fired)."

    async def _cmd_stop(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Stop Generation command (/stop). Stops active AI generation task."""
        purge = args.has_flag("purge", "p")
        canceled = await self.cancel_generation(chat_id, purge=purge)
        status = "Active AI generation stopped." if canceled else "No active AI generation running for this chat."
        if purge:
            status += " Uncommitted output purged."
        return status

    async def _cmd_send(self, args: CLIArgs, user_id: int, chat_id: int, event) -> Optional[str]:
        """Instant Send command (/send). Cancels active generation and starts fresh query."""
        drop_previous = args.has_flag("drop-previous", "d")
        await self.cancel_generation(chat_id, purge=drop_previous)

        msg_text = args.raw_tail
        for f in ["--drop-previous", "-d"]:
            msg_text = msg_text.replace(f, "").strip()

        if self.ai_manager:
            trigger_id = getattr(event.message, "id", None) if event else None
            asyncio.create_task(self.ai_manager.handle_query(str(chat_id), event.input_chat if event else None, trigger_msg_id=trigger_id))
            return None

        return "Error: AI Manager is not bound."

    async def _cmd_message(self, args: CLIArgs, user_id: int, chat_id: int, event) -> Optional[str]:
        """Message command (/message or /msg). Delivers text with optional embedded XML tags."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied. Required rank: ADMIN (80+)."

        msg_text = args.raw_tail
        if not msg_text:
            return "Usage: /message <text_or_xml_action_tags>"

        target_reply_id = getattr(event.message, "id", None) if event else None

        if self.ai_manager and hasattr(self.ai_manager, "executor"):
            _, _ = await self.ai_manager.executor.execute_response(msg_text, event.input_chat if event else None, target_reply_id, str(chat_id))
            return None
        elif self.client:
            from utils import safe_telegram_html
            formatted = safe_telegram_html(msg_text)
            await self.client.send_message(chat_id, formatted, parse_mode="html", reply_to=target_reply_id)
            return None
        return "Error: Client is not initialized."

    async def _cmd_help(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Help command (/help). Displays available user and admin commands."""
        query = args.positional[0].lower() if args.positional else "all"

        user_cmds = [
            "/q [--no-save/-n] [text] — Send message without triggering AI response",
            "/stop [--purge/-p] — Stop active AI generation in current chat",
            "/send [--drop-previous/-d] [text] — Instant query to AI, resetting previous task",
            "/help [all/user/admin/command/category] — Display help catalog"
        ]

        admin_cmds = [
            "/admin [set/reset/info] [user_id/@username] [rank] — Manage user ranks & permissions",
            "/config [get/set/list] [key] [value] — Inspect or update configuration",
            "/prompt [filename] [replace/insert_after/insert_before/delete] [pattern] [text] — Edit prompt files",
            "/message [text_with_xml_tags] — Execute XML action tags and deliver text",
            "/shell [command] — Execute bash/shell command in sandbox",
            "/telegram [method] [args_json] — Execute Telethon or raw TL action",
            "/run [code] — Execute Python script in Sandbox VM",
            "/sql [query] — Execute raw SQL query",
            "/request [method] [url] [json_data] — Send HTTP request",
            "/log [get/set] [lines/category/level] — Read or adjust log settings",
            "/command /tool /tag /service /cron /timer /trigger — Manage system elements"
        ]

        is_admin = await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN)

        if query == "user":
            return "=== User Commands ===\n" + "\n".join(user_cmds)
        elif query == "admin" and is_admin:
            return "=== Admin Commands ===\n" + "\n".join(admin_cmds)
        else:
            res = ["=== Baziliksina Commands Catalog ===", "\n--- User Commands ---"]
            res.extend(user_cmds)
            if is_admin:
                res.append("\n--- Admin Commands ---")
                res.extend(admin_cmds)
            return "\n".join(res)

    # --- ADMIN COMMAND HANDLERS ---
    async def _cmd_admin(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Admin rank manager command (/admin)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied. Required rank: ADMIN (80+)."

        if not args.positional:
            return "Usage: /admin [set/reset/info] [user_id/@username] [rank_number] [perms_json]"

        action = args.positional[0].lower()
        target_user = args.positional[1] if len(args.positional) > 1 else str(user_id)

        if action == "info":
            info = await permission_manager.get_user_rank_info(target_user)
            return f"Rank Info for '{target_user}':\n- Numerical Rank: {info['rank']}\n- Source: {info['source']}\n- Permissions: {info['permissions']}"

        elif action == "set":
            if len(args.positional) < 3:
                return "Usage: /admin set [user_id/@username] [rank_number] [perms_json]"
            try:
                new_rank = int(args.positional[2])
            except ValueError:
                return "Error: rank_number must be an integer (e.g. 80 for ADMIN, 50 for PRIORITY)."

            perms_list = None
            if len(args.positional) > 3:
                try: perms_list = json.loads(args.positional[3])
                except Exception: perms_list = [args.positional[3]]

            can_do, msg = await permission_manager.can_promote_or_demote(user_id, target_user, new_rank)
            if not can_do:
                return f"Error: {msg}"

            if self.db:
                await self.db.save_user_rank(target_user, new_rank, perms_list)
                return f"Success. Rank for '{target_user}' set to {new_rank}."

        elif action == "reset":
            if self.db:
                await self.db.delete_user_rank(target_user)
                return f"Success. Rank for '{target_user}' reset to default."

        return "Error: Invalid admin action. Choose 'set', 'reset', or 'info'."

    async def _cmd_config(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Config manager command (/config)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."

        if not args.positional:
            return "Usage: /config [get/set/list] [key] [value]"

        action = args.positional[0].lower()
        if action == "get" and len(args.positional) > 1:
            key = args.positional[1].upper()
            val = getattr(config, key, "Not Found")
            return f"Config '{key}': {val}"
        elif action == "set" and len(args.positional) > 2:
            key = args.positional[1].upper()
            val_str = args.positional[2]
            try: parsed_val = json.loads(val_str)
            except Exception: parsed_val = val_str

            setattr(config, key, parsed_val)
            if self.db:
                await self.db.save_setting(key, parsed_val)
            return f"Success. Config '{key}' updated to: {parsed_val}"
        elif action == "list":
            return f"Total active config parameters: {len(dir(config))}"

        return "Usage: /config [get/set/list] [key] [value]"

    async def _cmd_prompt(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Prompt file contextual editor command (/prompt)."""
        if not permission_manager.can_ai_perform("TAGS", "EDIT") and not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."

        if len(args.positional) < 2:
            return "Usage: /prompt [filename] [replace/insert_after/insert_before/delete] [pattern] [new_text]"

        filename = args.positional[0]
        operation = args.positional[1].lower()

        if not filename.endswith(".txt") or "/" in filename or "\\" in filename:
            return "Error: Invalid prompt filename."

        file_path = config.BASE_DIR / "config" / filename
        if not file_path.exists():
            return f"Error: Prompt file '{filename}' not found."

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            pattern = args.positional[2] if len(args.positional) > 2 else ""
            new_text = args.positional[3] if len(args.positional) > 3 else ""

            if operation == "replace":
                updated = re.sub(pattern, new_text, content)
            elif operation == "insert_after":
                updated = re.sub(rf"({re.escape(pattern)})", rf"\1\n{new_text}", content)
            elif operation == "insert_before":
                updated = re.sub(rf"({re.escape(pattern)})", rf"{new_text}\n\1", content)
            elif operation == "delete":
                updated = re.sub(pattern, "", content)
            else:
                return "Error: Unknown operation. Choose replace, insert_after, insert_before, delete."

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(updated)

            return f"Success. Prompt file '{filename}' updated via operation '{operation}'."
        except Exception as e:
            return f"Error editing prompt file: {str(e)}"

    async def _cmd_shell(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Shell execution command (/shell)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        cmd_str = args.raw_tail
        if not cmd_str:
            return "Usage: /shell [command]"
        from tools import run_sandboxed_command
        return await run_sandboxed_command(command=cmd_str)

    async def _cmd_telegram(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Telegram action command (/telegram)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        if not args.positional:
            return "Usage: /telegram [method_name] [args_json]"
        method = args.positional[0]
        args_json = args.positional[1] if len(args.positional) > 1 else "{}"
        from tools import execute_telegram_action
        return await execute_telegram_action(method_name=method, args_json=args_json)

    async def _cmd_run(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Python execution command (/run)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        code_str = args.raw_tail
        if not code_str:
            return "Usage: /run [python code]"
        from tools import execute_python_code
        return await execute_python_code(code=code_str)

    async def _cmd_sql(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """SQL query command (/sql)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        sql_str = args.raw_tail
        if not sql_str:
            return "Usage: /sql [query]"
        from tools import execute_sql_query
        return await execute_sql_query(sql=sql_str)

    async def _cmd_request(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """HTTP Request command (/request)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        if len(args.positional) < 2:
            return "Usage: /request [GET/POST/PUT/DELETE] [url] [json_data]"
        method = args.positional[0]
        url = args.positional[1]
        data = args.positional[2] if len(args.positional) > 2 else None
        from tools import send_http_request
        return await send_http_request(method=method, url=url, data_json=data)

    async def _cmd_log(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Log manager command (/log). Read or adjust log parameters and threshold levels."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        
        action = args.positional[0].lower() if args.positional else "get"

        if action == "get":
            limit = int(args.positional[1]) if len(args.positional) > 1 else 100
            filter_cat = args.positional[2].lower() if len(args.positional) > 2 else None
            filter_level = args.positional[3].upper() if len(args.positional) > 3 else None

            log_file = config.BASE_DIR / "bot.log"
            if not log_file.exists():
                return "Log file not found."

            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            filtered = []
            for line in lines[-limit*2:]:
                if filter_cat and filter_cat not in line.lower():
                    continue
                if filter_level and filter_level not in line:
                    continue
                filtered.append(line)

            return "".join(filtered[-limit:]) if filtered else "No matching log entries found."

        elif action == "set":
            if len(args.positional) < 2:
                return "Usage: /log set [DEBUG/INFO/WARNING/ERROR] [logger_name]"
            new_level_str = args.positional[1].upper()
            target_logger_name = args.positional[2] if len(args.positional) > 2 else None

            level_map = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL
            }
            if new_level_str not in level_map:
                return "Error: Invalid log level. Choose DEBUG, INFO, WARNING, ERROR, CRITICAL."

            target_level = level_map[new_level_str]
            if target_logger_name:
                logging.getLogger(target_logger_name).setLevel(target_level)
                return f"Success. Log level for '{target_logger_name}' set to {new_level_str}."
            else:
                logging.getLogger().setLevel(target_level)
                return f"Success. Root log level set to {new_level_str}."

        return "Usage: /log [get/set] [lines/category/level]"

    # --- FULL ELEMENT MANAGERS (CRUD) ---
    async def _cmd_element_command(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Custom commands manager (/command)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list" and self.db:
            cmds = await self.db.get_all_custom_commands()
            return f"Custom Commands ({len(cmds)}):\n" + "\n".join([f"- /{c['name']} [{c['category']}]: {c['help_text'] or 'No help'}" for c in cmds])
        elif subcmd == "add" and len(args.positional) > 2 and self.db:
            name = args.positional[1]
            code = args.raw_tail.split(args.positional[1], 1)[1].strip()
            await self.db.save_custom_command(name, code, help_text="Custom command", category="general")
            return f"Success. Custom command /{name} added."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            name = args.positional[1]
            deleted = await self.db.delete_custom_command(name)
            return f"Success. Command /{name} deleted." if deleted else f"Error: Command /{name} not found."
        elif subcmd == "info" and len(args.positional) > 1 and self.db:
            cmd = await self.db.get_custom_command(args.positional[1])
            if cmd:
                return f"Command /{cmd['name']} Info:\n- Category: {cmd['category']}\n- Help: {cmd['help_text']}\n- Code:\n```python\n{cmd['code']}\n```"
            return f"Error: Command /{args.positional[1]} not found."

        return "Usage: /command [list/add/delete/info] [name] [code]"

    async def _cmd_element_tool(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Tools manager (/tool)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list":
            tools_list = registry.get_all_tools()
            return f"Registered Tools ({len(tools_list)}):\n" + "\n".join([f"- {t.name} [{t.category}] {'(Custom)' if t.is_custom else ''}" for t in tools_list])
        elif subcmd == "info" and len(args.positional) > 1:
            t = registry.get(args.positional[1])
            if t:
                return f"Tool '{t.name}':\n- Category: {t.category}\n- Description: {t.description}\n- Custom: {t.is_custom}"
            return f"Error: Tool '{args.positional[1]}' not found."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            name = args.positional[1]
            deleted = await self.db.delete_custom_tool(name)
            if deleted:
                registry.unregister(name)
                return f"Success. Custom tool '{name}' deleted."
            return f"Error: Tool '{name}' not found or is a protected root tool."

        return "Usage: /tool [list/info/delete] [name]"

    async def _cmd_element_tag(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Tags manager (/tag)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list":
            tags_list = tag_block_registry.get_all()
            return f"Registered Tags/Blocks ({len(tags_list)}):\n" + "\n".join([f"- <{t.name}> [{t.type}] {'(Custom)' if t.is_custom else ''}" for t in tags_list])
        elif subcmd == "info" and len(args.positional) > 1:
            t = tag_block_registry.get(args.positional[1])
            if t:
                return f"Tag/Block '<{t.name}>':\n- Type: {t.type}\n- Description: {t.description}\n- Custom: {t.is_custom}"
            return f"Error: Tag '<{args.positional[1]}>' not found."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            name = args.positional[1]
            deleted = await self.db.delete_custom_tag_block(name)
            if deleted:
                tag_block_registry.unregister(name)
                return f"Success. Custom tag '<{name}>' deleted."
            return f"Error: Tag '<{name}>' not found or is protected."

        return "Usage: /tag [list/info/delete] [name]"

    async def _cmd_element_service(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Services manager (/service)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list":
            services = service_manager.list_services()
            return f"Registered Services ({len(services)}):\n" + "\n".join([f"- {s['name']} [{s['status']}] {'(Custom)' if s['is_custom'] else ''}" for s in services])
        elif subcmd == "start" and len(args.positional) > 1:
            ok = await service_manager.start_service(args.positional[1])
            return f"Service '{args.positional[1]}' started." if ok else f"Error starting service '{args.positional[1]}'."
        elif subcmd == "stop" and len(args.positional) > 1:
            ok = await service_manager.stop_service(args.positional[1])
            return f"Service '{args.positional[1]}' stopped." if ok else f"Error stopping service '{args.positional[1]}'."
        elif subcmd == "add" and len(args.positional) > 2 and self.db:
            name = args.positional[1]
            code = args.raw_tail.split(args.positional[1], 1)[1].strip()
            await self.db.save_custom_service(name, code, description="Custom Service", status="stopped")
            service_manager.register_service(name, code, description="Custom Service", is_custom=True)
            return f"Success. Custom service '{name}' created."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            name = args.positional[1]
            await service_manager.stop_service(name)
            deleted = await self.db.delete_custom_service(name)
            return f"Success. Service '{name}' deleted." if deleted else f"Error: Service '{name}' not found."

        return "Usage: /service [list/start/stop/add/delete] [name] [code]"

    async def _cmd_element_cron(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Cron jobs manager (/cron)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list":
            cron_jobs = service_manager.list_cron_jobs()
            return f"Registered Cron Jobs ({len(cron_jobs)}):\n" + "\n".join([f"- {c['name']} [{c['schedule_spec']}] - Status: {c['status']}" for c in cron_jobs])
        elif subcmd == "start" and len(args.positional) > 1:
            ok = await service_manager.start_cron_job(args.positional[1])
            return f"Cron job '{args.positional[1]}' started." if ok else f"Error starting cron job '{args.positional[1]}'."
        elif subcmd == "stop" and len(args.positional) > 1:
            ok = await service_manager.stop_cron_job(args.positional[1])
            return f"Cron job '{args.positional[1]}' stopped." if ok else f"Error stopping cron job '{args.positional[1]}'."
        elif subcmd == "add" and len(args.positional) > 3 and self.db:
            name = args.positional[1]
            spec = args.positional[2]
            code = args.raw_tail.split(args.positional[2], 1)[1].strip()
            await self.db.save_custom_cron_job(name, spec, code, description="Custom Cron Job")
            service_manager.register_cron_job(name, spec, code, description="Custom Cron Job", is_custom=True)
            await service_manager.start_cron_job(name)
            return f"Success. Custom cron job '{name}' created and started."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            name = args.positional[1]
            await service_manager.stop_cron_job(name)
            deleted = await self.db.delete_custom_cron_job(name)
            return f"Success. Cron job '{name}' deleted." if deleted else f"Error: Cron job '{name}' not found."

        return "Usage: /cron [list/start/stop/add/delete] [name] [schedule_spec] [code]"

    async def _cmd_element_timer(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Timers manager (/timer)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list" and self.db:
            timers = await self.db.get_pending_timers()
            return f"Pending Timers ({len(timers)}):\n" + "\n".join([f"- ID {t[0]} | Chat: {t[1]} | Exec: {t[2]} | Task: {t[3]}" for t in timers])
        elif subcmd == "add" and len(args.positional) > 2 and self.db:
            delay = int(args.positional[1])
            desc = args.positional[2]
            code = args.positional[3] if len(args.positional) > 3 else None
            await self.db.add_timer(str(chat_id), delay, desc, code)
            return f"Success. Timer created for {delay}s."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            t_id = int(args.positional[1])
            await self.db.delete_timer(t_id)
            return f"Success. Timer {t_id} deleted."

        return "Usage: /timer [list/add/delete] [delay_seconds/id] [description] [code]"

    async def _cmd_element_trigger(self, args: CLIArgs, user_id: int, chat_id: int, event) -> str:
        """Triggers manager (/trigger)."""
        if not await permission_manager.has_permission(user_id, required_rank=RankLevel.ADMIN):
            return "Error: Permission denied."
        subcmd = args.positional[0].lower() if args.positional else "list"

        if subcmd == "list" and self.db:
            triggers = await self.db.get_active_triggers(str(chat_id))
            return f"Active Triggers ({len(triggers)}):\n" + "\n".join([f"- ID {t[0]} | Type: {t[1]} | Val: {t[2]} | Task: {t[3]}" for t in triggers])
        elif subcmd == "add" and len(args.positional) > 3 and self.db:
            t_type = args.positional[1]
            t_val = args.positional[2]
            desc = args.positional[3]
            code = args.positional[4] if len(args.positional) > 4 else None
            await self.db.add_trigger(str(chat_id), t_type, t_val, desc, code)
            return f"Success. Trigger added."
        elif subcmd == "delete" and len(args.positional) > 1 and self.db:
            t_id = int(args.positional[1])
            await self.db.delete_trigger(t_id)
            return f"Success. Trigger {t_id} deleted."

        return "Usage: /trigger [list/add/delete] [type] [value] [description] [code]"


# Global singleton instance
command_manager = CommandManager()
