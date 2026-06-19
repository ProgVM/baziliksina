# bot.py
import sys
import json
import os
import time
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl import types as tl_types

# Import our modules
from config import (
    API_ID, API_HASH, SESSION_PATH, WORKSPACE_DIR, BOOTSTRAP_DATABASE, DEBOUNCE_DELAY, 
    DUPLICATE_CACHE_SIZE, PROFILE_UPDATE_INTERVAL, TIMERS_LOOP_INTERVAL, VM_STDOUT_NOTICE_LIMIT, 
    BOT_AVATAR_NAME, TELEGRAM_CONNECTION_RETRIES, TELEGRAM_RETRY_DELAY, 
    TELEGRAM_AUTO_RECONNECT, TELEGRAM_TIMEOUT
)
from db_manager import DBManager
from gemini_manager import GeminiManager, entity_cache
from parser import parse_message_payload, parse_reply_metadata, parse_sender_info, parse_and_cache_user_metadata, parse_and_cache_chat_metadata
from downloader import download_and_cache_media
from proxy_manager import proxy_rotator
import services
import tools

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BazilikBot")

# Parse proxy settings dynamically from config for Telethon client proxying
proxy_param = proxy_rotator.get_telethon_proxy()

# Managers initialization
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

# Global cache of AI account and events
me = None
processed_msg_ids = set()

# Strict incremental counter for debouncing against one-millisecond races
debounce_counter = 0

# Buffer for accumulating fast messages {chat_id: {"last_time": float, "entity": InputPeer}} (strictly int keys)
message_buffers = {}

# Processing queues to avoid parallel duplicate generations (strictly int keys)
generating_chats = set()
pending_buffers = {}

# Cache of the last update time of profiles and chats in memory (once per PROFILE_UPDATE_INTERVAL)
last_profile_updates = {}  # {user_id_int: timestamp_int}
last_chat_updates = {}     # {chat_id_int: timestamp_int}


async def run_and_log_sandbox_code(chat_id: int, code: str, source_type: str = "trigger", event = None):
    """Asynchronously runs code in the VM, prints results to the console, and writes them to the chat history for the AI."""
    result = await tools.execute_python_code(code, chat_id=chat_id, event=event)
    logger.info(f"--- VM background code execution result ({source_type}) ---\n{result}\n--------------------------------------------")
    
    p_result = result[:VM_STDOUT_NOTICE_LIMIT] + "..." if len(result) > VM_STDOUT_NOTICE_LIMIT else result
    notice_text = (
        f"[System notification: Autonomous Python code {source_type} finished execution]\n"
        f"Code:\n{code}\n\n"
        f"Execution result:\n{p_result}"
    ).strip()
    
    await db.save_message(str(chat_id), "user", notice_text)


async def check_and_run_triggers(chat_id: int, text: str, input_chat_entity, event) -> bool:
    """
    Scans active triggers for the chat, checks for text/regex matches 
    and triggers the corresponding AI wake-up or autonomous code in the VM.
    """
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
            asyncio.create_task(run_pending_query(chat_id, input_chat_entity))
            return True
            
    except Exception as tr_err:
        logger.error(f"Error processing triggers: {str(tr_err)}")
        
    return False


async def run_timers_loop():
    """Background service for periodic scanning and execution of timers from the DB."""
    import time
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
        await asyncio.sleep(TIMERS_LOOP_INTERVAL)


# Helper debouncer for the downloader and system triggers
def schedule_debounce_query(chat_id, entity, trigger_msg_id=None):
    chat_id = int(chat_id)
    current_time_id = time.time()
    if chat_id not in message_buffers:
        message_buffers[chat_id] = {}
        
    message_buffers[chat_id]["last_time"] = current_time_id
    message_buffers[chat_id]["entity"] = entity
    message_buffers[chat_id]["trigger_msg_id"] = trigger_msg_id # Store the triggering message ID

    async def wait_and_send_debounce(cid, trigger_time):
        await asyncio.sleep(DEBOUNCE_DELAY)
        if cid not in message_buffers:
            return
        if message_buffers[cid].get("last_time") != trigger_time:
            return
            
        entity_obj = message_buffers[cid]["entity"]
        t_msg_id = message_buffers[cid].get("trigger_msg_id")
        del message_buffers[cid]
        await run_pending_query(cid, entity_obj, trigger_msg_id=t_msg_id)

    asyncio.create_task(wait_and_send_debounce(chat_id, current_time_id))

# Handler for executing pending queries (strictly int types for queues)
async def run_pending_query(cid, entity, trigger_msg_id=None):
    cid_int = int(cid)
    generating_chats.add(cid_int)
    try:
        await ai_manager.handle_query(str(cid_int), entity, trigger_msg_id=trigger_msg_id)
    finally:
        generating_chats.discard(cid_int)
        if cid_int in pending_buffers:
            p_data = pending_buffers.pop(cid_int)
            schedule_debounce_query(cid_int, p_data["entity"], trigger_msg_id=p_data.get("trigger_msg_id"))


# --- Universal background tracking of reactions on posts, channels, and PMs ---
@client.on(events.Raw(types=[tl_types.UpdateMessageReactions, tl_types.UpdateBotMessageReaction, tl_types.UpdateBotMessageReactions]))
async def on_raw_reaction(event):
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
        
    rx_parts = []
    
    # 1. Case of UpdateMessageReactions / UpdateBotMessageReactions (contain ReactionCount)
    reactions_obj = getattr(event, "reactions", None)
    if reactions_obj:
        results = getattr(reactions_obj, "results", None)
        if results:
            for rc in results:
                if hasattr(rc.reaction, 'emoticon'):
                    rx_parts.append(f"'{rc.reaction.emoticon}' (x{rc.count})")
                elif hasattr(rc.reaction, 'document_id'):
                    rx_parts.append(f"[Custom emoji ID {rc.reaction.document_id}] (x{rc.count})")
        elif isinstance(reactions_obj, list):
            for rc in reactions_obj:
                if hasattr(rc.reaction, 'emoticon'):
                    rx_parts.append(f"'{rc.reaction.emoticon}' (x{rc.count})")
                elif hasattr(rc.reaction, 'document_id'):
                    rx_parts.append(f"[Custom emoji ID {rc.reaction.document_id}] (x{rc.count})")
                    
    # 2. Case of UpdateBotMessageReaction (contains a list of new reactions)
    new_reactions = getattr(event, "new_reactions", None)
    if new_reactions and isinstance(new_reactions, list):
        counts = {}
        for r in new_reactions:
            if hasattr(r, 'emoticon'):
                counts[r.emoticon] = counts.get(r.emoticon, 0) + 1
            elif hasattr(r, 'document_id'):
                key = f"Custom emoji ID {r.document_id}"
                counts[key] = counts.get(key, 0) + 1
        for k, v in counts.items():
            rx_parts.append(f"'{k}' (x{v})" if not k.startswith("Custom") else f"[{k}] (x{v})")

    reactions_str = ""
    if rx_parts:
        reactions_str = "[Reactions on message]: " + " | ".join(rx_parts)
        
    try:
        async with db.db.execute("SELECT meta_text, raw_meta_json FROM msgs_meta WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id)) as cursor:
            row = await cursor.fetchone()
            
        existing_meta_text = ""
        raw_meta = {}
        if row:
            existing_meta_text, raw_meta_raw = row
            raw_meta = json.loads(raw_meta_raw) if raw_meta_raw else {}
            
        # Remove previous reaction records from meta-text to avoid duplication
        lines = [line for line in existing_meta_text.split("\n") if not line.startswith("[Reactions on message]:")]
        if reactions_str:
            lines.append(reactions_str)
        new_meta_text = "\n".join(lines).strip()
        
        await db.save_msg_meta(chat_id, msg_id, meta_text=new_meta_text, raw_meta_dict=raw_meta)
        logger.info(f"Updated reactions for message #{msg_id} in chat {chat_id}: {reactions_str or 'reactions removed'}")
    except Exception as e:
        logger.error(f"Error saving updated reaction to DB: {str(e)}")


# New message handler
@client.on(events.NewMessage)
async def on_new_message(event):
    is_private = event.is_private
    mentioned = event.mentioned or (event.message.message and f"@{me.username}" in event.message.message)
    chat_id = int(event.chat_id)
    msg_id = event.message.id
    
    # 1. Protection against duplicate network packets across chats
    cache_key = (chat_id, msg_id)
    if cache_key in processed_msg_ids:
        logger.debug(f"Received duplicate message {msg_id} from chat {chat_id}. Skipping.")
        return
    processed_msg_ids.add(cache_key)
    if len(processed_msg_ids) > DUPLICATE_CACHE_SIZE:
        processed_msg_ids.clear()

    # Guarantee obtaining the chat's InputPeer immediately upon entry
    input_chat_entity = await event.get_input_chat()
    entity_cache[chat_id] = input_chat_entity

    # Auto-read incoming messages (only if PM or mentioned)
    if is_private or mentioned:
        try:
            await event.mark_read()
        except Exception as e:
            logger.debug(f"Failed to mark message as read: {str(e)}")

    # 2. Universal processing and detailed saving of ALL outgoing messages
    if event.sender_id == me.id:
        text = await parse_message_payload(client, db, event.message)
        logger.info(f"Recording outgoing message {msg_id} in chat {chat_id}: '{text[:100]}...'")
        
        # Download and cache any outgoing media files similarly to incoming ones
        media_info = await download_and_cache_media(client, event.message, is_private=True, mentioned=True)
        await db.save_message(str(chat_id), "model", text, media_info, msg_id)
        return

    # Background update of Premium metadata and avatars of sender and chat once every PROFILE_UPDATE_INTERVAL
    now_ts = int(time.time())
    sender = await event.get_sender()
    if sender and getattr(sender, "id", None):
        s_id = int(sender.id)
        if s_id not in last_profile_updates or (now_ts - last_profile_updates[s_id]) > PROFILE_UPDATE_INTERVAL:
            last_profile_updates[s_id] = now_ts
            asyncio.create_task(parse_and_cache_user_metadata(client, db, sender))
            
    c_id = int(chat_id)
    if c_id not in last_chat_updates or (now_ts - last_chat_updates[c_id]) > PROFILE_UPDATE_INTERVAL:
        last_chat_updates[c_id] = now_ts
        chat_ent = await event.get_chat()
        asyncio.create_task(parse_and_cache_chat_metadata(client, db, chat_ent))

    # 3. Synchronous management of buffering timers (STRICTLY BEFORE ANY AWAIT)
    global debounce_counter
    debounce_counter += 1
    current_trigger_id = debounce_counter
    
    if chat_id not in message_buffers:
        message_buffers[chat_id] = {}
        
    message_buffers[chat_id]["last_time"] = current_trigger_id
    message_buffers[chat_id]["entity"] = input_chat_entity
    message_buffers[chat_id]["trigger_msg_id"] = msg_id # Capture the triggering message ID

    # 4. Processing incoming messages
    text = await parse_message_payload(client, db, event.message)
    media_info = await download_and_cache_media(client, event.message, is_private, mentioned)

    meta_prefix = f"[Message ID: {msg_id}]\n"
    sender_info = parse_sender_info(sender, event.message)

    is_channel_pm = False
    if isinstance(event.message.peer_id, tl_types.PeerChannel) and not event.is_group and not event.message.post:
        is_channel_pm = True

    if event.is_group:
        chat_title = getattr(event.chat, 'title', 'Group')
        meta_prefix += f"[Group: '{chat_title}' | Sender: {sender_info}]\n"
    elif event.message.post:
        channel_title = getattr(event.chat, 'title', 'Channel')
        meta_prefix += f"[Channel post: '{channel_title}']\n"
    elif is_channel_pm:
        channel_title = getattr(sender, 'title', 'Channel')
        meta_prefix += f"[Private Chat with CHANNEL | Title: '{channel_title}' | ID: {event.message.peer_id.channel_id}]\n"
    else:
        meta_prefix += f"[Private Chat | Sender: {sender_info}]\n"

    if event.message.is_reply:
        reply_meta = await parse_reply_metadata(event.message, chat_id, client, db)
        meta_prefix += reply_meta

    full_prompt_text = f"{meta_prefix}{text}".strip()

    if me.username and f"@{me.username}" in full_prompt_text:
        full_prompt_text = full_prompt_text.replace(f"@{me.username}", "").strip()

    logger.info(f"Message {msg_id} saved to chat history {chat_id}.")
    await db.save_message(str(chat_id), "user", full_prompt_text, media_info, msg_id)

    if await check_and_run_triggers(chat_id, text, input_chat_entity, event):
        return

    # Start Debounce generation of AI response in all chats during activity lull
    async def wait_and_send(cid, trigger_time):
        await asyncio.sleep(DEBOUNCE_DELAY)
        
        if cid not in message_buffers:
            return
        if message_buffers[cid].get("last_time") != trigger_time:
            logger.debug(f"Canceling outdated generation branch for chat {cid} (new messages arrived).")
            return

        entity = message_buffers[cid]["entity"]
        t_msg_id = message_buffers[cid].get("trigger_msg_id")
        del message_buffers[cid]

        if cid in generating_chats:
            logger.info(f"Chat {cid} is busy generating. Adding to the pending queue.")
            pending_buffers[cid] = {"entity": entity, "trigger_msg_id": t_msg_id}
            return

        generating_chats.add(cid)
        try:
            await ai_manager.handle_query(str(cid), entity, trigger_msg_id=t_msg_id) # Pass the original trigger ID
        finally:
            generating_chats.discard(cid)
            if cid in pending_buffers:
                p_data = pending_buffers.pop(cid)
                schedule_debounce_query(cid, p_data["entity"], trigger_msg_id=p_data.get("trigger_msg_id"))

    asyncio.create_task(wait_and_send(chat_id, current_trigger_id))


# Message edit handler
@client.on(events.MessageEdited)
async def on_message_edited(event):
    is_private = event.is_private
    mentioned = event.mentioned or (event.message.message and f"@{me.username}" in event.message.message)
    
    if event.sender_id == me.id:
        return

    try:
        await event.mark_read()
    except Exception as e:
        logger.debug(f"Failed to mark edited message as read: {str(e)}")

    chat_id = int(event.chat_id)
    msg_id = event.message.id

    # Guarantee obtaining the chat's InputPeer immediately upon entry
    input_chat_entity = await event.get_input_chat()
    entity_cache[chat_id] = input_chat_entity
    
    # 1. Read the PREVIOUS version of the message from the database
    prev_text = "unknown"
    prev_media = "was absent"
    try:
        async with db.db.execute("SELECT text, media_info FROM messages WHERE chat_id = ? AND msg_id = ?", (str(chat_id), msg_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                prev_text, prev_media_raw = row
                if prev_media_raw:
                    p_media_data = json.loads(prev_media_raw)
                    prev_media = f"file '{p_media_data.get('mime_type')}'"
    except Exception as db_err:
        logger.error(f"Failed to retrieve old version during editing: {str(db_err)}")

    # Get the full text of the edited message
    new_text = await parse_message_payload(client, db, event.message)
    media_info = await download_and_cache_media(client, event.message, is_private, mentioned)

    if me.username and f"@{me.username}" in new_text:
        new_text = new_text.replace(f"@{me.username}", "").strip()

    logger.info(f"Message {msg_id} updated (edit/reaction) in chat {chat_id}.")
    await db.update_message_text(str(chat_id), msg_id, new_text, media_info)

    # Background update of Premium metadata and avatars of sender and chat once every PROFILE_UPDATE_INTERVAL
    now_ts = int(time.time())
    sender = await event.get_sender()
    if sender and getattr(sender, "id", None):
        s_id = int(sender.id)
        if s_id not in last_profile_updates or (now_ts - last_profile_updates[s_id]) > PROFILE_UPDATE_INTERVAL:
            last_profile_updates[s_id] = now_ts
            asyncio.create_task(parse_and_cache_user_metadata(client, db, sender))
            
    c_id = int(chat_id)
    if c_id not in last_chat_updates or (now_ts - last_chat_updates[c_id]) > PROFILE_UPDATE_INTERVAL:
        last_chat_updates[c_id] = now_ts
        chat_ent = await event.get_chat()
        asyncio.create_task(parse_and_cache_chat_metadata(client, db, chat_ent))

    # 2. Check triggers on edit
    if await check_and_run_triggers(chat_id, new_text, input_chat_entity, event):
        return

    reply_meta = ""
    if event.message.is_reply:
        reply_meta = await parse_reply_metadata(event.message, chat_id, client, db)

    sender_info = parse_sender_info(sender, event.message)
    
    # Parse inline buttons on change
    buttons_summary = ""
    if event.message.reply_markup and hasattr(event.message.reply_markup, 'rows'):
        buttons_text = []
        for row in event.message.reply_markup.rows:
            row_btns = []
            for btn in row.buttons:
                btn_info = f"'{btn.text}'"
                if hasattr(btn, 'data') and btn.data:
                    try:
                        btn_info += f" (callback_data: '{btn.data.decode('utf-8')}')"
                    except Exception:
                        btn_info += f" (callback_hex: '{btn.data.hex()}')"
                elif hasattr(btn, 'url') and btn.url:
                    btn_info += f" (url: '{btn.url}')"
                row_btns.append(btn_info)
            buttons_text.append(" | ".join(row_btns))
        if buttons_text:
            buttons_summary = "\n[Inline buttons in this message]:\n" + "\n".join(buttons_text)

    notice_text = (
        f"[System notification: Sender {sender_info} edited message {msg_id}]\n"
        f"--- PREVIOUS STATE ---\n"
        f"Text: '{prev_text}'\n"
        f"Media: {prev_media}\n"
        f"--- NEW STATE ---\n"
        f"Text with metadata: '{reply_meta}{new_text}'\n"
        f"{buttons_summary}"
    ).strip()

    await db.save_message(str(chat_id), "user", notice_text, media_info)

# Message deletion handler
@client.on(events.MessageDeleted)
async def on_message_deleted(event):
    # Background periodic update of group/channel profile when deletions are detected
    now_ts = int(time.time())
    chat_id = event.chat_id
    if chat_id:
        c_id = int(chat_id)
        if c_id not in last_chat_updates or (now_ts - last_chat_updates[c_id]) > PROFILE_UPDATE_INTERVAL:
            last_chat_updates[c_id] = now_ts
            try:
                chat_ent = await event.get_chat()
                asyncio.create_task(parse_and_cache_chat_metadata(client, db, chat_ent))
            except Exception as e:
                logger.debug(f"Failed to update chat metadata upon message deletion: {str(e)}")

    for msg_id in event.deleted_ids:
        orig_text = None
        role = None
        
        try:
            async with db.db.execute(
                "SELECT chat_id, role, text FROM messages WHERE msg_id = ? LIMIT 1", 
                (msg_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    db_chat_id, role, orig_text = row
                    
                    cid_int = int(db_chat_id)
                    if not chat_id:
                        chat_id = cid_int
                    
                    if orig_text and (orig_text.startswith("{") or "FunctionCall" in orig_text or "FunctionResponse" in orig_text):
                        continue

                    logger.info(f"Message deletion detected {msg_id} in chat {cid_int}. Text: '{orig_text[:50]}...'")
                    await db.update_message_text(str(cid_int), msg_id, f"[Message deleted by user]: {orig_text}")
                    
                    notice_text = f"[System notification: Message #{msg_id} ('{orig_text[:50]}...') was deleted by the sender]"
                    await db.save_message(str(cid_int), "user", notice_text)
                    
                    is_private_chat = cid_int > 0
                    was_ai_related = (role == "model") or (orig_text and f"@{me.username}" in orig_text)
                    
                    if is_private_chat or was_ai_related:
                        input_chat_entity = entity_cache.get(cid_int)
                        if not input_chat_entity:
                            input_chat_entity = await client.get_input_entity(cid_int)
                            entity_cache[cid_int] = input_chat_entity
                            
                        if input_chat_entity and cid_int not in generating_chats:
                            logger.info(f"Starting generation of response to deletion in chat {cid_int}...")
                            generating_chats.add(cid_int)
                            try:
                                await ai_manager.handle_query(str(cid_int), input_chat_entity)
                            finally:
                                generating_chats.discard(cid_int)
        except Exception as e:
            logger.error(f"Error processing message deletion {msg_id}: {str(e)}")


async def main():
    global me
    logger.info("Connecting to asynchronous database...")
    await db.connect()
    
    # Initialization of cross-references for the tools module
    tools.client = client
    tools.db = db
    tools.ai_manager = ai_manager
    tools.key_manager = ai_manager.key_manager
    tools.pollinations_key_manager = ai_manager.pollinations_key_manager
    tools.bot_callback_fn = ai_manager.handle_query
    
    # Register system tools in the global registry at startup
    tools.register_system_tools()
    
    # Sync custom tools from the SQLite database
    from registry import sync_custom_tools_with_db
    await sync_custom_tools_with_db(db)
    
    # Asynchronously read and restore the saved working key from SQLite DB
    await ai_manager.key_manager.load_saved_index()
    await ai_manager.pollinations_key_manager.load_saved_index()
    
    logger.info("Starting Telegram userbot...")
    await client.start()
    logger.info("Userbot successfully authorized!")
    
    me = await client.get_me()
    
    # [FIRST RUN]: Bootstrap AI dialogue history if allowed by the BOOTSTRAP_DATABASE setting
    if BOOTSTRAP_DATABASE:
        await services.bootstrap_database_if_empty(client, db)
    
    # Download AI's own avatar for autonomous analysis at startup
    try:
        photos = await client.get_profile_photos(me, limit=1)
        if photos:
            logger.info("Downloading AI's own account avatar to bot_workspace...")
            await client.download_media(photos[0], file=str(WORKSPACE_DIR / BOT_AVATAR_NAME))
    except Exception as e:
        logger.error(f"Failed to download AI avatar: {str(e)}")
    
    # Start infinite background processes
    asyncio.create_task(services.keep_alive_online(client))
    asyncio.create_task(services.connection_monitor(client, db, WORKSPACE_DIR, processed_msg_ids, entity_cache, schedule_debounce_query))
    asyncio.create_task(run_timers_loop())
    try:
        await client.run_until_disconnected()
    finally:
        await db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        sys.exit(0)
