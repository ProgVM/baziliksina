# services.py
import asyncio
import logging
from telethon.tl.functions.account import UpdateStatusRequest

from config import DIALOGS_LIMIT, BOOTSTRAP_MESSAGES_LIMIT, MISSED_MESSAGES_LIMIT, KEEP_ALIVE_INTERVAL, CONNECTION_MONITOR_INTERVAL, BOOTSTRAP_TRIGGER_GENERATION, CATCH_UP_TRIGGER_GENERATION
from parser import parse_message_payload, parse_and_cache_user_metadata, parse_and_cache_chat_metadata

logger = logging.getLogger("Services")


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
            chat_id = str(dialog.id)
            chat_entity = dialog.entity
            logger.info(f"Importing chat history: '{dialog.name}' ({chat_id})...")

            try:
                await parse_and_cache_chat_metadata(client, db, chat_entity)
            except Exception:
                pass

            messages_to_save = []
            async for msg in client.iter_messages(chat_entity, limit=BOOTSTRAP_MESSAGES_LIMIT):  # [FIXED]: Using BOOTSTRAP_MESSAGES_LIMIT
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

                role = "model" if msg.sender_id == me.id else "user"
                
                if role == "user" and msg.sender:
                    try:
                        await parse_and_cache_user_metadata(client, db, msg.sender)
                    except Exception:
                        pass

                parsed_text = await parse_message_payload(client, db, msg)
                await db.save_message(chat_id, role, parsed_text, None, msg.id)

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
    logger.info("Starting the missed messages catch-up process...")
    try:
        me = await client.get_me()
        async for dialog in client.iter_dialogs(limit=DIALOGS_LIMIT):
            chat_id = str(dialog.id)
            
            # Find the ID of the absolute last saved message
            async with db.db.execute(
                "SELECT msg_id FROM messages WHERE chat_id = ? AND msg_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (chat_id,)
            ) as cursor:
                row = await cursor.fetchone()
            
            if not row:
                continue
            
            last_msg_id = row[0]
            missed_messages = []
            async for msg in client.iter_messages(dialog.id, min_id=last_msg_id, limit=MISSED_MESSAGES_LIMIT):  # [FIXED]: Using MISSED_MESSAGES_LIMIT
                missed_messages.append(msg)
            
            if not missed_messages:
                continue
            
            # Instantly record missed IDs in processed_msg_ids SYNCHRONOUSLY,
            # so that the NewMessage handler does not process them repeatedly during our awaits
            for msg in missed_messages:
                processed_msg_ids.add(msg.id)
            
            logger.info(f"Found {len(missed_messages)} missed messages in chat '{dialog.name}' ({chat_id}).")
            missed_messages.reverse()
            newly_saved_count = 0
            
            for msg in missed_messages:
                # Check if the message is already in the DB
                async with db.db.execute(
                    "SELECT id FROM messages WHERE chat_id = ? AND msg_id = ?",
                    (chat_id, msg.id)
                ) as check_cursor:
                    exists = await check_cursor.fetchone()
                if exists:
                    continue
                
                role = "model" if msg.sender_id == me.id else "user"
                if role == "user" and msg.sender:
                    try:
                        await parse_and_cache_user_metadata(client, db, msg.sender)
                    except Exception:
                        pass
                
                parsed_text = await parse_message_payload(client, db, msg)
                await db.save_message(chat_id, role, parsed_text, None, msg.id)
                newly_saved_count += 1
            
            # If new messages are caught up and there are incoming ones among them, schedule a debounce
            if newly_saved_count > 0:
                has_incoming_user_message = any(msg.sender_id != me.id for msg in missed_messages)
                if has_incoming_user_message:
                    entity = dialog.entity
                    entity_cache[dialog.id] = entity
                    logger.info(f"Debounce response scheduled for {newly_saved_count} missed messages in chat '{dialog.name}'...")
                    if CATCH_UP_TRIGGER_GENERATION:
                        run_pending_query_fn(int(chat_id), entity)
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
