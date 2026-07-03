# tools/system_tools.py
import os
import json
import asyncio
import logging
import re
import inspect
import time
from typing import Any, List
import urllib.parse
import httpx

import config
from config import (
    WORKSPACE_DIR, TOR_HOST, TOR_CONTROL_PORT, TOR_PASSWORD, TOR_ROTATION_TIMEOUT,
    POLLINATIONS_MAX_ATTEMPTS, TOR_MAX_CONSECUTIVE_FAILURES, SQL_SELECT_LIMIT,
    SQL_STDOUT_CHAR_LIMIT, SANDBOX_COMMAND_CHAR_LIMIT, OWNER_ID
)
import tools

logger = logging.getLogger("Tools.System")

FORBIDDEN_SHELL_REGEX = re.compile(
    r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|tools\.py|sandbox\.py|utils\.py|downloader\.py)", 
    re.IGNORECASE
)

async def rotate_tor_ip() -> bool:
    """Asynchronously connects to local Tor and sends a NEWNYM signal."""
    try:
        coro = asyncio.open_connection(TOR_HOST, TOR_CONTROL_PORT)
        reader, writer = await asyncio.wait_for(coro, timeout=TOR_ROTATION_TIMEOUT)
        writer.write(f'AUTHENTICATE "{TOR_PASSWORD}"\r\n'.encode('utf-8'))
        await writer.drain()
        resp1 = await reader.readline()
        if b"250" in resp1:
            writer.write(b'SIGNAL NEWNYM\r\n')
            await writer.drain()
            resp2 = await reader.readline()
            if b"250" in resp2:
                logger.info("Tor successfully rotated IP!")
                writer.close()
                await writer.wait_closed()
                return True
        writer.close()
        await writer.wait_closed()
    except Exception as e:
        logger.warning(f"Failed to send Tor rotation signal: {str(e)}")
    return False

async def call_pollinations_api(url: str, params: dict, timeout: float) -> httpx.Response:
    """Universal asynchronous method for executing requests to the Pollinations API."""
    from proxy_manager import proxy_rotator
    proxy_url = proxy_rotator.get_proxy("pollinations")
    num_keys = len(tools.pollinations_key_manager.keys) if (tools.pollinations_key_manager and tools.pollinations_key_manager.keys) else 1
    max_attempts = max(POLLINATIONS_MAX_ATTEMPTS, num_keys * 4)
    consecutive_ip_failures = 0
    tor_rotated_last_turn = False
    
    for attempt in range(max_attempts):
        current_key = await tools.pollinations_key_manager.get_active_key() if tools.pollinations_key_manager else ""
        req_params = params.copy()
        if current_key:
            req_params["key"] = current_key
        else:
            req_params.pop("key", None)
            
        logger.info(f"Request to Pollinations (Attempt {attempt+1}/{max_attempts}, Key: {current_key[:10]}...)...")
        try:
            if proxy_url:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client_httpx:
                    resp = await client_httpx.get(url, params=req_params)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client_httpx:
                    resp = await client_httpx.get(url, params=req_params)
            if resp.status_code in [401, 402, 429]:
                logger.warning(f"Pollinations returned error {resp.status_code} for key {current_key[:10]}...")
                is_pk_key = current_key.startswith("pk_") or current_key.startswith("plln_pk_")
                if is_pk_key:
                    if tor_rotated_last_turn:
                        consecutive_ip_failures += 1
                    else:
                        consecutive_ip_failures = 0
                    if consecutive_ip_failures < TOR_MAX_CONSECUTIVE_FAILURES:
                        tor_rotated = await rotate_tor_ip()
                        if tor_rotated:
                            tor_rotated_last_turn = True
                            continue
                if tools.pollinations_key_manager:
                    await tools.pollinations_key_manager.rotate_key_async()
                consecutive_ip_failures = 0
                tor_rotated_last_turn = False
                continue
            tor_rotated_last_turn = False
            return resp
        except Exception as e:
            logger.error(f"Pollinations request execution failed: {str(e)}")
            is_pk_key = current_key.startswith("pk_") or current_key.startswith("plln_pk_")
            if is_pk_key:
                if tor_rotated_last_turn:
                    consecutive_ip_failures += 1
                else:
                    consecutive_ip_failures = 0
                if consecutive_ip_failures < TOR_MAX_CONSECUTIVE_FAILURES:
                    tor_rotated = await rotate_tor_ip()
                    if tor_rotated:
                        tor_rotated_last_turn = True
                        await asyncio.sleep(1.0)
                        continue
            if tools.pollinations_key_manager and len(tools.pollinations_key_manager.keys) > 1:
                await tools.pollinations_key_manager.rotate_key_async()
            consecutive_ip_failures = 0
            tor_rotated_last_turn = False
            await asyncio.sleep(1.0)

class AIToolKitSystem:
    async def execute_python_code(self, code: str, **kwargs) -> str:
        """Executes asynchronous Python code in a safe isolated sandbox VM and returns the result."""
        from sandbox import AsyncSandbox
        try:
            cid = tools.current_chat_id.get()
        except LookupError:
            cid = None
        sandbox = AsyncSandbox(WORKSPACE_DIR, tools.client, tools.db, tools.ai_manager, chat_id=cid)
        return await sandbox.execute(code)

    def no_op_ignore(self, reason: str, continue_loop: bool = False, **kwargs) -> str:
        """Finishes the current generation step immediately without sending any text messages to the chat."""
        logger.info(f"Dialogue ignored. Reason: {reason} | Continue loop: {continue_loop}")
        return f"Dialogue successfully ignored. Reason: {reason} | Continue loop: {continue_loop}"

    async def execute_sql_query(self, sql: str, **kwargs) -> str:
        """Executes a raw SQL query on the configured local SQLite database."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            async with tools.db.db.execute(sql) as cursor:
                if cursor.description is not None:
                    rows = await cursor.fetchall()
                    cols = [d[0] for d in cursor.description]
                    results = [dict(zip(cols, row)) for row in rows[:SQL_SELECT_LIMIT]]
                    if not results:
                        return "Query executed. No matching rows found."
                    from utils import safe_serialize
                    out = safe_serialize(results)
                    return out[:SQL_STDOUT_CHAR_LIMIT] + "\n[Output truncated]" if len(out) > SQL_STDOUT_CHAR_LIMIT else out
                else:
                    await tools.db.db.commit()
                    rowcount = cursor.rowcount
                    lastrowid = cursor.lastrowid
                    res_parts = ["Query executed successfully. Transaction committed."]
                    if rowcount is not None and rowcount >= 0:
                        res_parts.append(f"Affected rows: {rowcount}")
                    if lastrowid is not None and lastrowid > 0:
                        res_parts.append(f"Last inserted row ID: {lastrowid}")
                    return "\n".join(res_parts)
        except Exception as e:
            return f"SQL Error: {str(e)}"

    async def run_sandboxed_command(self, command: str, **kwargs) -> str:
        """Runs a standard system bash/shell command securely in the sandbox."""
        if FORBIDDEN_SHELL_REGEX.search(command):
            return "Security error: This shell command contains blocked terms."
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(WORKSPACE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            res = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
            return res[:SANDBOX_COMMAND_CHAR_LIMIT] if len(res) > SANDBOX_COMMAND_CHAR_LIMIT else res if res else "Command finished with no output."
        except Exception as e:
            return f"Error executing shell command: {str(e)}"

    async def get_chat_history_from_db(self, chat_id: str, limit: int = 50, **kwargs) -> str:
        """Retrieves raw historical messages from the SQLite database for a specific chat."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            async with tools.db.db.execute(
                "SELECT role, text, timestamp FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (str(chat_id), limit)
            ) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                return f"No message history found in the DB for chat {chat_id}."
            rows.reverse()
            lines = [f"[{ts}] {role.upper()}: {text}" for role, text, ts in rows]
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading chat history: {str(e)}"

    async def get_telegram_object_info(self, entity_id: str, **kwargs) -> str:
        """Requests and returns comprehensive properties, names, bios, types, and raw JSON of any Telegram entity."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(entity_id, str):
                try: entity_id = int(entity_id)
                except ValueError: pass
            entity = await tools.client.get_entity(entity_id)
            e_type = type(entity).__name__
            details = [
                f"Entity Details:",
                f"- ID: {entity.id}",
                f"- Type: {e_type}"
            ]
            if hasattr(entity, "username") and entity.username:
                details.append(f"- Username: @{entity.username}")
            bio_ref = "None"
            if e_type == "User":
                details.append(f"- First Name: '{getattr(entity, 'first_name', '') or ''}'")
                details.append(f"- Last Name: '{getattr(entity, 'last_name', '') or ''}'")
                details.append(f"- IS BOT: {'Yes' if getattr(entity, 'bot', False) else 'No'}")
                details.append(f"- Is Premium: {'Yes' if getattr(entity, 'premium', False) else 'No'}")
                details.append(f"- Is Verified: {'Yes' if getattr(entity, 'verified', False) else 'No'}")
                if getattr(entity, "phone", None):
                    details.append(f"- Phone: {entity.phone}")
                meta = await tools.db.get_user_meta(str(entity.id)) if tools.db else None
                if meta:
                    bio_ref = meta.get("bio") or "None"
            elif e_type in ["Channel", "Chat"]:
                details.append(f"- Title: '{getattr(entity, 'title', '') or ''}'")
                is_group = getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False) or e_type == "Chat"
                details.append(f"- Subtype: {'Supergroup' if is_group else 'Channel'}")
                meta = await tools.db.get_chat_meta(str(entity.id)) if tools.db else None
                if meta:
                    bio_ref = meta.get("bio") or meta.get("description") or "None"
            details.append(f"- Bio/Description from cache: '{bio_ref}'")
            summary_text = "\n".join(details)
            from utils import safe_serialize
            raw_json = safe_serialize(entity)
            return f"{summary_text}\n\n=== Raw MTProto API Payload (JSON) ===\n{raw_json}"
        except Exception as e:
            return f"Error retrieving Telegram object info: {str(e)}"

    async def get_telegram_message_details(self, chat_id: str, message_id: int, **kwargs) -> str:
        """Requests and returns properties, text, formatting, reactions, and button layout of a message."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            message = await tools.client.get_messages(chat_id, ids=message_id)
            if not message:
                return f"Error: Message #{message_id} not found."
            from parser import parse_sender_info, get_media_type_description
            sender_info = parse_sender_info(message.sender, message)
            details = [
                f"Message #{message.id} Properties:",
                f"- Sender: {sender_info}",
                f"- Date Sent: {message.date}",
                f"- Last Edited: {message.edit_date or 'Never'}",
                f"- Raw Text Content: '{message.message or ''}'"
            ]
            if message.is_reply:
                details.append(f"- Is Reply To: {message.reply_to.reply_to_msg_id}")
            media_desc = get_media_type_description(message) or "None"
            details.append(f"- Attached Media: {media_desc}")
            buttons_desc = []
            if message.reply_markup and hasattr(message.reply_markup, 'rows'):
                for r_idx, row in enumerate(message.reply_markup.rows):
                    row_btns = []
                    for b_idx, btn in enumerate(row.buttons):
                        btn_desc = f"Button [{b_idx},{r_idx}] | Text: '{btn.text}'"
                        if hasattr(btn, 'data') and btn.data:
                            try: btn_desc += f" | callback_data: '{btn.data.decode('utf-8')}'"
                            except Exception: btn_desc += f" | callback_hex: '{btn.data.hex()}'"
                        elif hasattr(btn, 'url') and btn.url:
                             btn_desc += f" | URL: '{btn.url}'"
                        row_btns.append(btn_desc)
                    buttons_desc.append(f"Row {r_idx}:\n  " + "\n  ".join(row_btns))
            buttons_summary = "\n".join(buttons_desc) if buttons_desc else "No inline buttons."
            details.append(f"- Inline Buttons Layout:\n{buttons_summary}")
            summary_text = "\n".join(details)
            from utils import safe_serialize
            raw_json = safe_serialize(message)
            return f"{summary_text}\n\n=== Raw MTProto API Payload (JSON) ===\n{raw_json}"
        except Exception as e:
            return f"Error retrieving message details: {str(e)}"

    async def upload_file_to_google(self, filename: str, timeout: float = None, **kwargs):
        """Uploads the specified file from the sandbox (bot_workspace) to Google Gemini servers."""
        if not tools.key_manager:
            return {"status": "error", "message": "Gemini KeyManager is not initialized."}
        if timeout is None:
            timeout = config.GOOGLE_UPLOAD_TIMEOUT
        file_path = WORKSPACE_DIR / os.path.basename(filename)
        if not file_path.exists() or not file_path.is_file():
            return {"status": "error", "message": f"File '{filename}' not found."}
        try:
            gemini_client = tools.key_manager.get_client()
            uploaded_file = await asyncio.wait_for(gemini_client.aio.files.upload(file=str(file_path.resolve())), timeout=timeout)
            from utils import wait_for_google_file_active
            if not await wait_for_google_file_active(gemini_client, uploaded_file.name):
                return {"status": "error", "message": "Google file processing timed out."}
            if tools.db:
                await tools.db.set_memory(uploaded_file.uri, uploaded_file.mime_type)
            return {
                "status": "success",
                "filename": filename,
                "google_uri": uploaded_file.uri,
                "mime_type": uploaded_file.mime_type,
                "message": f"File {filename} successfully uploaded. URI: {uploaded_file.uri}"
            }
        except Exception as e:
            return {"status": "error", "message": f"Error: {str(e)}"}

    async def create_or_update_custom_tool(self, name: str, category: str, description: str, code: str, **kwargs) -> str:
        """Creates a new or updates an existing custom dynamic AI tool at runtime."""
        if not tools.db:
            return "Error: Database is not initialized."
        try:
            await tools.db.save_custom_tool(name, category, description, code)
            from registry import compile_custom_tool, registry
            compiled_func = compile_custom_tool(name, code)
            registry.register(name=name, callable_func=compiled_func, category=category, description=description, is_custom=True)
            return f"Success. Custom tool '{name}' created/updated."
        except Exception as e:
            return f"Error: {str(e)}"

    async def delete_custom_tool(self, name: str, **kwargs) -> str:
        """Deletes a previously created custom dynamic AI tool."""
        if not tools.db:
            return "Error: Database is not initialized."
        if name in tools.ROOT_TOOL_CATEGORIES:
            return f"Error: Tool '{name}' is a system tool and cannot be deleted."
        try:
            deleted = await tools.db.delete_custom_tool(name)
            if not deleted:
                return f"Error: Custom tool '{name}' not found."
            from registry import registry
            registry.unregister(name)
            return f"Success. Custom tool '{name}' completely deleted."
        except Exception as e:
            return f"Error: {str(e)}"

# Export methods to module level
toolkit_sys = AIToolKitSystem()
for attr in dir(toolkit_sys):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_sys, attr)
