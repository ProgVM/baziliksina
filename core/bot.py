# core/bot.py
import sys
import json
import os
import time
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl import types as tl_types

from config import (
    API_ID, API_HASH, SESSION_PATH, WORKSPACE_DIR, BOOTSTRAP_DATABASE, DEBOUNCE_DELAY, 
    DUPLICATE_CACHE_SIZE, PROFILE_UPDATE_INTERVAL, TIMERS_LOOP_INTERVAL, VM_STDOUT_NOTICE_LIMIT, 
    BOT_AVATAR_NAME, TELEGRAM_CONNECTION_RETRIES, TELEGRAM_RETRY_DELAY, 
    TELEGRAM_AUTO_RECONNECT, TELEGRAM_TIMEOUT
)
import config
from db_manager import DBManager
from gemini_manager import GeminiManager, entity_cache
from permission_manager import permission_manager
from service_manager import service_manager
from command_manager import command_manager
from parser import parse_message_payload, parse_reply_metadata, parse_sender_info, parse_and_cache_user_metadata, parse_and_cache_chat_metadata
from downloader import download_and_cache_media
from proxy_manager import proxy_rotator
from server.server import start_web_server, stop_web_server
import services
import tools
from utils import should_process_message_event, should_process_reaction_event, load_feedback_template, send_message_safe, safe_telegram_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BazilikBot")

proxy_param = proxy_rotator.get_telethon_proxy()

db = DBManager()
client = TelegramClient(
    SESSION_PATH, 
    API_ID, 
    API_HASH, 
    proxy=proxy_param,
    connection_retries=TELEGRAM_CONNECTION_RETRIES,
    retry_delay=TELEGRAM_RETRY_DELAY,
    auto_reconnect=TELEGRAM_AUTO_RECONNECT,
    timeout=TELEGRAM_TIMEOUT
)
ai_manager = GeminiManager(client, db)

me = None
processed_msg_ids = tools.processed_msg_ids

debounce_counter = 0
message_buffers = {}
generating_chats = set()
pending_buffers = {}

last_profile_updates = {}
last_chat_updates = {}

split_command_buffers = {}

def has_unclosed_syntax(text: str) -> bool:
    """Checks if code or text has unclosed triple-quotes or unclosed brackets."""
    single_triple = text.count("'''")
    double_triple = text.count('"""')
    if single_triple % 2 != 0 or double_triple % 2 != 0:
        return True
    open_parens = text.count("(") - text.count(")")
    open_brackets = text.count("[") - text.count("]")
    open_braces = text.count("{") - text.count("}")
    return open_parens > 0 or open_brackets > 0 or open_braces > 0

async def upload_media_to_google_background(media_info_str):
    if not media_info_str:
        return
    try:
        media_data = json.loads(media_info_str)
        m_path = media_data.get("path")
        m_type = media_data.get("mime_type")
        if m_path and os.path.exists(m_path) and m_type:
            if "webm" in m_type or m_path.endswith(".webm"):
                return
            
            import hashlib
            file_hash = hashlib.md5(m_path.encode('utf-8')).hexdigest()
            cache_key = f"google_file_uri_{file_hash}"
            
            google_uri = await db.get_memory(cache_key)
            if not google_uri:
                logger.info(f"[Background Upload]: Uploading file '{m_path}' to Google Files API...")
                gemini_client = ai_manager.key_manager.get_client()
                uploaded_file = await gemini_client.aio.files.upload(file=m_path)
                google_uri = uploaded_file.uri
                
                from utils import wait_for_google_file_active
                if await wait_for_google_file_active(gemini_client, uploaded_file.name):
                    await db.set_memory(cache_key, google_uri)
                    await db.set_memory(google_uri, uploaded_file.mime_type)
                    logger.info(f"[Background Upload]: File successfully cached: {google_uri}")
    except Exception as e:
        logger.error(f"Error in background media upload: {str(e)}")

async def run_and_log_sandbox_code(chat_id: int, code: str, source_type: str = "trigger", event = None):
    result = await tools.execute_python_code(code, chat_id=chat_id, event=event)
    logger.info(f"--- VM background code execution result ({source_type}) ---\n{result}\n--------------------------------------------")
    
    p_result = result[:config.VM_STDOUT_NOTICE_LIMIT] + "..." if len(result) > config.VM_STDOUT_NOTICE_LIMIT else result
    
    template = load_feedback_template("vm_notification", "[System notification: Autonomous Python code {source_type} finished execution]\nCode:\n{code}\n\nExecution result:\n{p_result}")
    notice_text = template.replace("{source_type}", source_type).replace("{code}", code).replace("{p_result}", p_result).strip()
    
    await db.save_message(str(chat_id), "user", notice_text)

async def check_and_run_triggers(chat_id: int, text: str, input_chat_entity, event) -> bool:
    import re
    try:
        active_triggers = await db.get_active_triggers(str(chat_id))
        force_wake_up = False
        wake_reason = ""
        wake_code = None
        
        for t_id, t_type, t_val, t_action, t_code in active_triggers:
            if t_type == "word":
                try:
                    pattern = re.compile(t_val, re.IGNORECASE)
                    if pattern.search(text):
                        force_wake_up = True
                        wake_reason = f"[Trigger fired on regular expression '{t_val}'! Your task: {t_action}]"
                        wake_code = t_code
                        await db.delete_trigger(t_id)
                        break
                except Exception as re_err:
                    logger.error(f"Invalid trigger regex '{t_val}': {str(re_err)}")
            elif t_type == "messages":
                try:
                    rem_msgs = int(t_val) - 1
                    if rem_msgs <= 0:
                        force_wake_up = True
                        wake_reason = f"[Message count trigger fired! Your task: {t_action}]"
                        wake_code = t_code
                        await db.delete_trigger(t_id)
                    else:
                        await db.db.execute("UPDATE triggers SET value = ? WHERE id = ?", (str(rem_msgs), t_id))
                        await db.db.commit()
                except Exception as te:
                    logger.error(f"Error decrementing message trigger: {str(te)}")
        
        if force_wake_up:
            if wake_code and wake_code.strip():
                logger.info("Trigger fired. Starting autonomous Python code from trigger...")
                asyncio.create_task(run_and_log_sandbox_code(chat_id, wake_code, source_type="trigger", event=event))
            
            logger.info(f"Wake trigger fired in chat {chat_id}. Starting AI generation...")
            await db.save_message(str(chat_id), "user", wake_reason)
            trigger_id = event.message.id if hasattr(event, "message") else None
            asyncio.create_task(run_pending_query(chat_id, input_chat_entity, trigger_msg_id=trigger_id))
            return True
            
    except Exception as tr_err:
        logger.error(f"Error processing triggers: {str(tr_err)}")
        
    return False

async def run_timers_loop():
    logger.info("Starting background service for persistent timers...")
    while True:
        try:
            now = int(time.time())
            pending_timers = await db.get_pending_timers()
            for t_id, chat_id, execute_at, action, code in pending_timers:
                if now >= execute_at:
                    logger.info(f"Timer ID triggered {t_id} for chat {chat_id}. Executing...")
                    await db.delete_timer(t_id)
                    
                    cid_int = int(chat_id)
                    if code:
                        logger.info("Timer contains autonomous Python code. Starting VM...")
                        asyncio.create_task(run_and_log_sandbox_code(cid_int, code, source_type="timer"))
                    else:
                        entity = entity_cache.get(cid_int)
                        if not entity:
                            entity = await client.get_input_entity(cid_int)
                            entity_cache[cid_int] = entity
                        
                        wake_text = f"[System notification: Timer triggered! Task: {action}. Perform this action in the chat right now.]"
                        await db.save_message(str(cid_int), "user", wake_text)
                        asyncio.create_task(run_pending_query(cid_int, entity))
        except Exception as e:
            logger.error(f"Error in timers loop: {str(e)}")
        await asyncio.sleep(config.TIMERS_LOOP_INTERVAL)

def schedule_debounce_query(chat_id, entity, trigger_msg_id=None):
    chat_id = int(chat_id)
    current_time_id = time.time()
    if chat_id not in message_buffers:
        message_buffers[chat_id] = {}
        
    message_buffers[chat_id]["last_time"] = current_time_id
    message_buffers[chat_id]["entity"] = entity
    message_buffers[chat_id]["trigger_msg_id"] = trigger_msg_id

    async def wait_and_send_debounce(cid, trigger_time):
        await asyncio.sleep(config.DEBOUNCE_DELAY)
        if cid not in message_buffers or message_buffers[cid].get("last_time") != trigger_time:
            return
            
        entity_obj = message_buffers[cid]["entity"]
        t_msg_id = message_buffers[cid].get("trigger_msg_id")
        del message_buffers[cid]
        await run_pending_query(cid, entity_obj, trigger_msg_id=t_msg_id)

    asyncio.create_task(wait_and_send_debounce(chat_id, current_time_id))

async def run_pending_query_after_delay(cid, entity, trigger_msg_id):
    await asyncio.sleep(config.QUEUE_PROMOTION_DELAY)
    await run_pending_query(cid, entity, trigger_msg_id=trigger_msg_id)

async def run_pending_query(cid, entity, trigger_msg_id=None):
    cid_int = int(cid)
    generating_chats.add(cid_int)
    try:
        await ai_manager.handle_query(str(cid_int), entity, trigger_msg_id=trigger_msg_id)
    finally:
        generating_chats.discard(cid_int)
        if cid_int in pending_buffers:
            p_data = pending_buffers.pop(cid_int)
            queued_msg_id = p_data.get("trigger_msg_id")
            
            processed_id = getattr(tools, "last_processed_user_msg_id", {}).get(str(cid_int))
            if queued_msg_id and processed_id and queued_msg_id <= processed_id:
                logger.info(f"Queued message #{queued_msg_id} was already processed (up to #{processed_id}). Skipping redundant queue run.")
            else:
                asyncio.create_task(run_pending_query_after_delay(cid_int, p_data["entity"], queued_msg_id))

@client.on(events.Raw(types=[tl_types.UpdateMessageReactions, tl_types.UpdateBotMessageReaction, tl_types.UpdateBotMessageReactions]))
async def on_raw_reaction(event):
    global me
    peer = getattr(event, "peer", None)
    msg_id = getattr(event, "msg_id", None)
    if not peer or not msg_id:
        return
        
    chat_id = None
    if isinstance(peer, tl_types.PeerUser):
        chat_id = str(peer.user_id)
    elif isinstance(peer, tl_types.PeerChat):
        chat_id = str(peer.chat_id)
        if not chat_id.startswith("-"):
            chat_id = f"-{chat_id}"
    elif isinstance(peer, tl_types.PeerChannel):
        chat_id = str(peer.channel_id)
        if not chat_id.startswith("-"):
            chat_id = f"-100{chat_id}"
            
    if not chat_id:
        return
        
    if me is None:
        try: me = await client.get_me()
        except Exception: return

    rx_parts = []
    reactions_obj = getattr(event, "reactions", None)
    if reactions_obj:
        results = getattr(reactions_obj, "results", None)
        if results:
            for rc in results:
                if hasattr(rc.reaction, 'emoticon'):
                    emoji_val = rc.reaction.emoticon
                    if should_process_reaction_event(emoji_val, me.id, me.id, True):
                        rx_parts.append(f"'{emoji_val}' (x{rc.count})")
                elif hasattr(rc.reaction, 'document_id'):
                    emoji_val = str(rc.reaction.document_id)
                    if should_process_reaction_event(emoji_val, me.id, me.id, True):
                        rx_parts.append(f"[Custom emoji ID {emoji_val}] (x{rc.count})")

    new_reactions = getattr(event, "new_reactions", None)
    if new_reactions and isinstance(new_reactions, list):
        counts = {}
        for r in new_reactions:
            actor_id = getattr(event, "actor_id", me.id)
            if hasattr(r, 'emoticon'):
                emoji_val = r.emoticon
                if should_process_reaction_event(emoji_val, actor_id, me.id, True):
                    counts[emoji_val] = counts.get(emoji_val, 0) + 1
            elif hasattr(r, 'document_id'):
                emoji_val = str(r.document_id)
                if should_process_reaction_event(emoji_val, actor_id, me.id, True):
                    key = f"Custom emoji ID {emoji_val}"
                    counts[key] = counts.get(key, 0) + 1
        for k, v in counts.items():
            rx_parts.append(f"'{k}' (x{v})" if not k.startswith("Custom") else f"[{k}] (x{v})")

    if not rx_parts:
        return

    reactions_str = "[Reactions on message]: " + " | ".join(rx_parts)
        
    try:
        async with db.db.execute("SELECT meta_text, raw_meta_json FROM msgs_meta WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id)) as cursor:
            row = await cursor.fetchone()
            
        existing_meta_text = ""
        raw_meta = {}
        if row:
            existing_meta_text, raw_meta_raw = row
            raw_meta = json.loads(raw_meta_raw) if raw_meta_raw else {}
            
        lines = [line for line in existing_meta_text.split("\n") if not line.startswith("[Reactions on message]:")]
        if reactions_str:
            lines.append(reactions_str)
        new_meta_text = "\n".join(lines).strip()
        
        await db.save_msg_meta(chat_id, msg_id, meta_text=new_meta_text, raw_meta_dict=raw_meta)
        logger.info(f"Updated reactions for message #{msg_id} in chat {chat_id}: {reactions_str}")
    except Exception as e:
        logger.error(f"Error saving updated reaction to DB: {str(e)}")

@client.on(events.NewMessage)
async def on_new_message(event):
    global me
    if me is None:
        try: me = await client.get_me()
        except Exception: return

    is_private = event.is_private
    chat_id = int(event.chat_id)
    msg_id = event.message.id
    
    cache_key = (chat_id, msg_id)
    if cache_key in processed_msg_ids:
        return
    processed_msg_ids.add(cache_key)
    if len(processed_msg_ids) > DUPLICATE_CACHE_SIZE:
        processed_msg_ids.clear()

    input_chat_entity = await event.get_input_chat()
    entity_cache[chat_id] = input_chat_entity

    buffer_key = (chat_id, event.sender_id)

    # 1. Check if incoming message is a continuation of a split command
    if buffer_key in split_command_buffers:
        buf = split_command_buffers[buffer_key]
        if buf.get("task") and not buf["task"].done():
            buf["task"].cancel()

        buf["payload"] += "\n" + (event.message.message or "")
        logger.info(f"Stitched continuation message for split command in chat {chat_id}. Total length: {len(buf['payload'])} chars.")

        async def _wait_and_execute(key):
            await asyncio.sleep(1.2)
            data = split_command_buffers.pop(key, None)
            if data:
                full_cmd = data["payload"]
                first_id = data["msg_id"]
                logger.info(f"Executing fully stitched split CLI Command in chat {chat_id}: '{full_cmd[:60]}...'")
                cmd_output = await command_manager.execute_pipeline(full_cmd, event.sender_id, chat_id, event)
                if cmd_output:
                    formatted = safe_telegram_html(cmd_output)
                    await send_message_safe(client, input_chat_entity, formatted, reply_to=first_id, parse_mode="html")

        buf["task"] = asyncio.create_task(_wait_and_execute(buffer_key))
        return

    # 2. Check for CLI Commands Execution (Processed for both incoming and outgoing manual commands)
    raw_payload = event.message.message or ""
    if raw_payload.strip().startswith("/"):
        if has_unclosed_syntax(raw_payload) or len(raw_payload) > 3500:
            logger.info(f"Detected unclosed syntax or long command in message #{msg_id}. Buffering for split continuation...")
            
            async def _wait_and_execute_initial(key):
                await asyncio.sleep(1.2)
                data = split_command_buffers.pop(key, None)
                if data:
                    full_cmd = data["payload"]
                    first_id = data["msg_id"]
                    logger.info(f"Executing buffered CLI Command in chat {chat_id}: '{full_cmd[:60]}...'")
                    cmd_output = await command_manager.execute_pipeline(full_cmd, event.sender_id, chat_id, event)
                    if cmd_output:
                        formatted = safe_telegram_html(cmd_output)
                        await send_message_safe(client, input_chat_entity, formatted, reply_to=first_id, parse_mode="html")

            task = asyncio.create_task(_wait_and_execute_initial(buffer_key))
            split_command_buffers[buffer_key] = {
                "payload": raw_payload,
                "msg_id": msg_id,
                "task": task
            }
            return

        logger.info(f"CLI Command detected in message #{msg_id} of chat {chat_id}: '{raw_payload[:60]}'")
        cmd_output = await command_manager.execute_pipeline(raw_payload, event.sender_id, chat_id, event)
        if cmd_output:
            formatted = safe_telegram_html(cmd_output)
            await send_message_safe(client, input_chat_entity, formatted, reply_to=msg_id, parse_mode="html")
        
        # Prevent AI generation when a command is executed
        if not getattr(config, "TRIGGER_ON_COMMANDS", False):
            return

    if not await should_process_message_event(event, me, "save", db):
        return

    # Auto-read incoming messages based on filter matrix
    try:
        from utils import should_send_read_acknowledge
        is_triggered = is_private or getattr(event, "mentioned", False)
        if await should_send_read_acknowledge(event, me, db, is_trigger_fired=is_triggered):
            await event.mark_read()
    except Exception as e:
        logger.debug(f"Failed to mark message as read: {str(e)}")

    is_outgoing = event.sender_id == me.id
    if is_outgoing:
        text = await parse_message_payload(client, db, event.message)
        reply_meta = await parse_reply_metadata(event.message, chat_id, client, db) if event.message.is_reply else ""
        
        existing_meta = await db.get_msg_meta(str(chat_id), msg_id)
        existing_meta_text = existing_meta.get("meta_text") if existing_meta else ""
        combined_meta = f"{reply_meta.strip()}\n{existing_meta_text.strip()}".strip()
        await db.save_msg_meta(str(chat_id), msg_id, meta_text=combined_meta, raw_meta_dict=existing_meta.get("raw_meta") if existing_meta else None)
        
        logger.info(f"Recording outgoing message {msg_id} in chat {chat_id}: '{text[:100]}...'")
        media_info = await download_and_cache_media(client, event.message, is_private=True, mentioned=True)
        if media_info:
            asyncio.create_task(upload_media_to_google_background(media_info))
        await db.save_message(str(chat_id), "model", text, media_info, msg_id)

        if not getattr(config, "TRIGGER_ON_OUTGOING_MANUAL_MESSAGES", False):
            return

    now_ts = int(time.time())
    sender = await event.get_sender()
    if sender and getattr(sender, "id", None) and config.SAVE_USER_METADATA:
        s_id = int(sender.id)
        if (s_id not in last_profile_updates or (now_ts - last_profile_updates[s_id]) > config.PROFILE_UPDATE_INTERVAL):
            last_profile_updates[s_id] = now_ts
            asyncio.create_task(parse_and_cache_user_metadata(client, db, sender))
            
    c_id = int(chat_id)
    if config.SAVE_CHAT_METADATA and (c_id not in last_chat_updates or (now_ts - last_chat_updates[c_id]) > config.PROFILE_UPDATE_INTERVAL):
        last_chat_updates[c_id] = now_ts
        chat_ent = await event.get_chat()
        asyncio.create_task(parse_and_cache_chat_metadata(client, db, chat_ent))

    text = await parse_message_payload(client, db, event.message)
    media_info = await download_and_cache_media(client, event.message, is_private, event.mentioned)
    if media_info:
        asyncio.create_task(upload_media_to_google_background(media_info))

    meta_prefix = f"[Message ID: {msg_id}]\n"
    sender_role = "Member"
    custom_tag = "None"
    if event.is_group and sender:
        try:
            permissions = await client.get_permissions(event.chat_id, sender)
            if getattr(permissions, 'is_creator', False): sender_role = "Owner/Creator"
            elif getattr(permissions, 'is_admin', False): sender_role = "Admin"
            from telethon.tl.functions.channels import GetParticipantRequest
            res = await client(GetParticipantRequest(channel=event.chat_id, participant=sender))
            custom_tag = getattr(res.participant, "rank", None) or "None"
        except Exception: pass
    tag_info = f" | Member Tag: '{custom_tag}'" if custom_tag != "None" else ""
    sender_info = f"{parse_sender_info(sender, event.message)} | Group Role: {sender_role}{tag_info}"

    if event.is_group:
        chat_title = getattr(event.chat, 'title', 'Group')
        meta_prefix += f"[Group: '{chat_title}' | Sender: {sender_info}]\n"
    else:
        meta_prefix += f"[Private Chat | Sender: {sender_info}]\n"

    if event.message.is_reply:
        reply_meta = await parse_reply_metadata(event.message, chat_id, client, db)
        meta_prefix += reply_meta

    existing_meta = await db.get_msg_meta(str(chat_id), msg_id)
    existing_meta_text = existing_meta.get("meta_text") if existing_meta else ""
    combined_meta = f"{meta_prefix.strip()}\n{existing_meta_text.strip()}".strip()
    await db.save_msg_meta(str(chat_id), msg_id, meta_text=combined_meta, raw_meta_dict=existing_meta.get("raw_meta") if existing_meta else None)

    if me.username and f"@{me.username}" in text:
        text = text.replace(f"@{me.username}", "").strip()

    logger.info(f"Message {msg_id} saved to chat history {chat_id}.")
    await db.save_message(str(chat_id), "user", text, media_info, msg_id)

    if await check_and_run_triggers(chat_id, text, input_chat_entity, event):
        return

    if not await should_process_message_event(event, me, "trigger", db):
        return

    global debounce_counter
    debounce_counter += 1
    current_trigger_id = debounce_counter
    
    if chat_id in generating_chats:
        logger.info(f"Chat {chat_id} is busy generating. Queuing message {msg_id} directly.")
        pending_buffers[chat_id] = {"entity": input_chat_entity, "trigger_msg_id": msg_id}
        return
        
    if chat_id not in message_buffers:
        message_buffers[chat_id] = {}
        
    message_buffers[chat_id]["last_time"] = current_trigger_id
    message_buffers[chat_id]["entity"] = input_chat_entity
    message_buffers[chat_id]["trigger_msg_id"] = msg_id

    async def wait_and_send(cid, trigger_time):
        await asyncio.sleep(config.DEBOUNCE_DELAY)
        if cid not in message_buffers or message_buffers[cid].get("last_time") != trigger_time:
            return

        entity = message_buffers[cid]["entity"]
        t_msg_id = message_buffers[cid].get("trigger_msg_id")
        del message_buffers[cid]

        if cid in generating_chats:
            pending_buffers[cid] = {"entity": entity, "trigger_msg_id": t_msg_id}
            return

        generating_chats.add(cid)
        try:
            await ai_manager.handle_query(str(cid), entity, trigger_msg_id=t_msg_id)
        finally:
            generating_chats.discard(cid)
            if cid in pending_buffers:
                p_data = pending_buffers.pop(cid)
                schedule_debounce_query(cid, p_data["entity"], trigger_msg_id=p_data.get("trigger_msg_id"))

    asyncio.create_task(wait_and_send(chat_id, current_trigger_id))

@client.on(events.MessageEdited)
async def on_message_edited(event):
    global me
    if me is None:
        try: me = await client.get_me()
        except Exception: return

    if event.sender_id == me.id:
        return

    chat_id = int(event.chat_id)
    msg_id = event.message.id

    input_chat_entity = await event.get_input_chat()
    entity_cache[chat_id] = input_chat_entity

    raw_payload = event.message.message or ""
    if raw_payload.strip().startswith("/"):
        cmd_output = await command_manager.execute_pipeline(raw_payload, event.sender_id, chat_id, event)
        if cmd_output:
            formatted = safe_telegram_html(cmd_output)
            await send_message_safe(client, input_chat_entity, formatted, reply_to=msg_id, parse_mode="html")
        if not getattr(config, "TRIGGER_ON_COMMANDS", False):
            return

    new_text = await parse_message_payload(client, db, event.message)
    media_info = await download_and_cache_media(client, event.message, event.is_private, event.mentioned)
    if media_info:
        asyncio.create_task(upload_media_to_google_background(media_info))

    await db.update_message_text(str(chat_id), msg_id, new_text, media_info)

@client.on(events.MessageDeleted)
async def on_message_deleted(event):
    for msg_id in event.deleted_ids:
        try:
            async with db.db.execute("SELECT chat_id, role, text FROM messages WHERE msg_id = ? LIMIT 1", (msg_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    db_chat_id, role, orig_text = row
                    cid_int = int(db_chat_id)
                    await db.update_message_text(str(cid_int), msg_id, f"[Message deleted by user]: {orig_text}")
        except Exception as e:
            logger.error(f"Error handling message deletion: {str(e)}")

async def main():
    global me
    logger.info("Connecting to asynchronous database...")
    await db.connect()
    
    try:
        from config import reload_config_from_db
        await reload_config_from_db(db)
        
        tools.client = client
        tools.db = db
        tools.ai_manager = ai_manager
        tools.key_manager = ai_manager.key_manager
        tools.pollinations_key_manager = ai_manager.pollinations_key_manager
        tools.bot_callback_fn = ai_manager.handle_query
        
        permission_manager.set_db_manager(db)
        service_manager.bind_core_references(db, client, ai_manager)
        command_manager.bind_core_references(db, client, ai_manager)

        tools.register_system_tools()
        
        from registry import sync_custom_tools_with_db, sync_custom_tags_blocks_with_db
        await sync_custom_tools_with_db(db)
        await sync_custom_tags_blocks_with_db(db)
        
        await ai_manager.key_manager.load_saved_index()
        await ai_manager.pollinations_key_manager.load_saved_index()
        
        logger.info("Starting Telegram userbot...")
        await client.start()
        logger.info("Userbot successfully authorized!")
        
        me = await client.get_me()

        # Register system background services
        service_manager.register_service("keep_alive", lambda: services.keep_alive_online(client), description="Keep Alive Online Service", is_custom=False)
        service_manager.register_service("connection_monitor", lambda: services.connection_monitor(client, db, WORKSPACE_DIR, processed_msg_ids, entity_cache, schedule_debounce_query), description="Connection Monitor", is_custom=False)
        service_manager.register_service("timers_loop", run_timers_loop, description="Persistent Timers Loop", is_custom=False)
        service_manager.register_service("web_server", lambda: start_web_server(client, db, ai_manager), description="RESTful Web Server", is_custom=False)

        await service_manager.start_service("keep_alive")
        await service_manager.start_service("connection_monitor")
        await service_manager.start_service("timers_loop")
        await service_manager.start_service("web_server")

        await service_manager.sync_with_db()
        
        if BOOTSTRAP_DATABASE:
            await services.bootstrap_database_if_empty(client, db, run_pending_query_fn=schedule_debounce_query)
        
        try:
            photos = await client.get_profile_photos(me, limit=1)
            if photos:
                await client.download_media(photos[0], file=str(WORKSPACE_DIR / BOT_AVATAR_NAME))
        except Exception as e:
            logger.error(f"Failed to download AI avatar: {str(e)}")
        
        await client.run_until_disconnected()
    finally:
        try:
            pending = asyncio.all_tasks()
            current = asyncio.current_task()
            for task in pending:
                if task is not current:
                    task.cancel()
            if pending:
                await asyncio.gather(*[t for t in pending if t is not current], return_exceptions=True)
        except Exception:
            pass
        try:
            await stop_web_server()
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        sys.exit(0)
