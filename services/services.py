# services/services.py
import asyncio
import logging
from telethon.tl.functions.account import UpdateStatusRequest

from config import (
    DIALOGS_LIMIT, BOOTSTRAP_MESSAGES_LIMIT, MISSED_MESSAGES_LIMIT, 
    KEEP_ALIVE_INTERVAL, CONNECTION_MONITOR_INTERVAL, 
    BOOTSTRAP_TRIGGER_GENERATION, CATCH_UP_TRIGGER_GENERATION
)
import config
from parser import parse_message_payload, parse_and_cache_user_metadata, parse_and_cache_chat_metadata

logger = logging.getLogger("Services")


class MessageEventWrapper:
    """Wraps a Telethon Message into an Event-like interface for filter and trigger evaluation."""
    def __init__(self, msg, chat_entity=None):
        self.message = msg
        self.chat_id = msg.chat_id
        self.sender_id = msg.sender_id
        self.is_private = getattr(msg, "is_private", False)
        self.is_group = getattr(msg, "is_group", False)
        self.is_channel = getattr(msg, "is_channel", False)
        self.mentioned = getattr(msg, "mentioned", False)
        self.chat = chat_entity
        self.input_chat = chat_entity


async def keep_alive_online(client):
    f"""Keeps the account status 'Online' every {KEEP_ALIVE_INTERVAL} seconds."""
    while True:
        try:
            await client(UpdateStatusRequest(offline=False))
            logger.debug("Status 'Online' successfully sent.")
        except Exception as e:
            logger.error(f"Error in keep_alive: {str(e)}")
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)


async def bootstrap_database_if_empty(client, db, run_pending_query_fn=None):
    f"""
    [FIRST RUN]: If the database is completely empty, this method scans the last {DIALOGS_LIMIT} chats
    and populates the local memory with the last {BOOTSTRAP_MESSAGES_LIMIT} messages from each dialog.
    """
    try:
        async with db.db.execute("SELECT COUNT(*) FROM messages") as cursor:
            count_row = await cursor.fetchone()
            if count_row and count_row[0] > 0:
                logger.info("Database already contains history. Skipping initial import.")
                return

        logger.info("--- FIRST BOT RUN DETECTED. STARTING CHAT HISTORY PRE-LOADING ---")
        me = await client.get_me()
        
        async for dialog in client.iter_dialogs(limit=DIALOGS_LIMIT):
            try:
                chat_id = str(dialog.id)
                chat_entity = dialog.entity
                logger.info(f"Importing chat history: '{dialog.name}' ({chat_id})...")

                try:
                    await parse_and_cache_chat_metadata(client, db, chat_entity)
                except Exception:
                    pass

                messages_to_save = []
                async for msg in client.iter_messages(chat_entity, limit=BOOTSTRAP_MESSAGES_LIMIT):
                    messages_to_save.append(msg)

                messages_to_save.reverse()

                for msg in messages_to_save:
                    async with db.db.execute(
                        "SELECT id FROM messages WHERE chat_id = ? AND msg_id = ?",
                        (chat_id, msg.id)
                    ) as check_c:
                        exists = await check_c.fetchone()
                    if exists:
                        continue

                    raw_text = msg.message or ""
                    if raw_text.strip().startswith("/") and not getattr(config, "TRIGGER_ON_COMMANDS", False):
                        continue

                    role = "model" if msg.sender_id == me.id else "user"
                    
                    if role == "user" and msg.sender:
                        try:
                            await parse_and_cache_user_metadata(client, db, msg.sender)
                        except Exception:
                            pass

                    parsed_text = await parse_message_payload(client, db, msg)
                    await db.save_message(chat_id, role, parsed_text, None, msg.id)
            except Exception as d_err:
                logger.warning(f"Failed to bootstrap dialog '{dialog.name}' ({dialog.id}): {str(d_err)}")

        logger.info("--- INITIAL CHAT HISTORY CATCH-UP SUCCESSFULLY COMPLETED! ---")
        # Trigger initial generation for the most recent active chat if requested
        if BOOTSTRAP_TRIGGER_GENERATION and run_pending_query_fn:
            try:
                async for dialog in client.iter_dialogs(limit=1):
                    logger.info(f"Triggering initial bootstrap generation for chat '{dialog.name}'...")
                    run_pending_query_fn(dialog.id, dialog.entity)
            except Exception as e:
                logger.error(f"Failed to trigger initial bootstrap generation: {str(e)}")
    except Exception as e:
        logger.error(f"Database bootstrap error: {str(e)}")


async def catch_up_missed_messages(client, db, workspace_dir, processed_msg_ids, entity_cache, run_pending_query_fn):
    """Background task to catch up on messages that arrived during inactivity or network failure."""
    logger.debug("Starting the missed messages catch-up process...")
    try:
        from utils import should_process_message_event, should_send_read_acknowledge
        import config

        me = await client.get_me()
        async for dialog in client.iter_dialogs(limit=DIALOGS_LIMIT):
            try:
                chat_id = str(dialog.id)
                cid_int = int(dialog.id)
                
                # Find the ID of the absolute last saved message
                async with db.db.execute(
                    "SELECT msg_id FROM messages WHERE chat_id = ? AND msg_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (chat_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                
                if not row:
                    continue
                
                last_msg_id = row[0]
                raw_missed = []
                async for msg in client.iter_messages(dialog.id, min_id=last_msg_id, limit=MISSED_MESSAGES_LIMIT):
                    raw_missed.append(msg)
                
                if not raw_missed:
                    continue
                
                # 1. Filter out messages that were already processed in memory by NewMessage handler
                missed_to_process = []
                for msg in raw_missed:
                    cache_key = (cid_int, msg.id)
                    if cache_key in processed_msg_ids:
                        continue
                    processed_msg_ids.add(cache_key)
                    missed_to_process.append(msg)
                
                if not missed_to_process:
                    continue

                missed_to_process.reverse()
                newly_saved_count = 0
                has_valid_trigger = False
                last_trigger_msg_id = None
                
                for msg in missed_to_process:
                    # Check if the message is already in the DB
                    async with db.db.execute(
                        "SELECT id FROM messages WHERE chat_id = ? AND msg_id = ?",
                        (chat_id, msg.id)
                    ) as check_cursor:
                        exists = await check_cursor.fetchone()
                    if exists:
                        continue
                    
                    is_from_me = msg.sender_id == me.id
                    role = "model" if is_from_me else "user"
                    raw_text = msg.message or ""
                    is_command = raw_text.strip().startswith("/")

                    # Do NOT treat commands as dialogue messages for AI if TRIGGER_ON_COMMANDS is False
                    if is_command and not getattr(config, "TRIGGER_ON_COMMANDS", False):
                        continue

                    msg_wrapper = MessageEventWrapper(msg, dialog.entity)

                    # Check save rules
                    if not await should_process_message_event(msg_wrapper, me, "save", db):
                        continue

                    if role == "user" and msg.sender:
                        try:
                            await parse_and_cache_user_metadata(client, db, msg.sender)
                        except Exception:
                            pass
                    
                    parsed_text = await parse_message_payload(client, db, msg)
                    await db.save_message(chat_id, role, parsed_text, None, msg.id)
                    newly_saved_count += 1

                    # Only flag trigger if it is an incoming user message that ACTUALLY fires AI triggers
                    if role == "user" and not is_command:
                        should_trigger = await should_process_message_event(msg_wrapper, me, "trigger", db)
                        if should_trigger:
                            has_valid_trigger = True
                            last_trigger_msg_id = msg.id

                # Auto-read missed messages in Telegram based on config filter matrix
                if newly_saved_count > 0:
                    try:
                        should_read = False
                        for m in missed_to_process:
                            if m.sender_id != me.id:
                                m_wrapper = MessageEventWrapper(m, dialog.entity)
                                is_m_triggered = m.is_private or getattr(m, "mentioned", False)
                                if not is_m_triggered:
                                    m_text = (m.message or "").lower()
                                    if me.first_name and me.first_name.lower() in m_text: is_m_triggered = True
                                    elif me.username and f"@{me.username.lower()}" in m_text: is_m_triggered = True
                                if await should_send_read_acknowledge(m_wrapper, me, db, is_trigger_fired=is_m_triggered):
                                    should_read = True
                                    break
                        if should_read:
                            await client.send_read_acknowledge(dialog.entity, max_id=missed_to_process[-1].id)
                    except Exception as read_ex:
                        logger.debug(f"Failed to mark caught-up messages as read: {str(read_ex)}")

                # Log only if new messages were actually saved to DB
                if newly_saved_count > 0:
                    logger.info(f"Caught up {newly_saved_count} new messages in chat '{dialog.name}' ({chat_id}).")

                # ONLY schedule debounce response if there was an actual valid AI trigger!
                if newly_saved_count > 0 and has_valid_trigger:
                    entity = dialog.entity
                    entity_cache[dialog.id] = entity
                    logger.info(f"Debounce response scheduled for {newly_saved_count} missed messages in chat '{dialog.name}'...")
                    if CATCH_UP_TRIGGER_GENERATION and run_pending_query_fn:
                        run_pending_query_fn(int(chat_id), entity, trigger_msg_id=last_trigger_msg_id)
            except Exception as d_err:
                logger.warning(f"Failed to catch up missed messages for dialog '{dialog.name}' ({dialog.id}): {str(d_err)}")
    except Exception as e:
        logger.error(f"History catch-up error: {str(e)}")


async def connection_monitor(client, db, workspace_dir, processed_msg_ids, entity_cache, run_pending_query_fn):
    """Telegram network monitoring for automatic history recovery after failures."""
    asyncio.create_task(catch_up_missed_messages(client, db, workspace_dir, processed_msg_ids, entity_cache, run_pending_query_fn))
    
    was_connected = True
    while True:
        await asyncio.sleep(CONNECTION_MONITOR_INTERVAL)
        try:
            is_connected = client.is_connected()
            if is_connected and not was_connected:
                logger.info("Network restored. Starting synchronization of missed correspondence...")
                asyncio.create_task(catch_up_missed_messages(client, db, workspace_dir, processed_msg_ids, entity_cache, run_pending_query_fn))
            was_connected = is_connected
        except Exception as e:
            logger.error(f"Error in network monitor: {str(e)}")
