# tools/telegram_tools.py
import os
import json
import asyncio
import logging
import re
import inspect
from typing import List, Any, Dict

import config
from config import BOT_RESPONSE_TIMEOUT, DEFAULT_RESULT_INDEX, BUTTON_CLICK_TIMEOUT, OWNER_ID, TELEGRAM_METHOD_BLACKLIST, WORKSPACE_DIR
import tools
from parser import parse_reply_markup

logger = logging.getLogger("Tools.Telegram")

class AIToolKitTelegram:
    async def send_agent_message(self, text: str, chat_id: str = None, reply_to_msg_id: int = None, reply_to_chat_id: str = None, quote_text: str = None, is_deleted_fallback: bool = False, fallback_sender_name: str = "User", fallback_sender_id: int = None, **kwargs) -> str:
        """Sends a message, a standard reply, a cross-chat reply, or a quote reply."""
        if not tools.client:
            return "Error: Telethon client is not initialized."

        if chat_id is None:
            try:
                chat_id = tools.current_chat_id.get()
            except LookupError:
                return "Error: Failed to determine target chat."

        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass

        if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
            text = await tools.ai_manager.executor.parse_execute_and_strip_tags(text, chat_id, reply_to_msg_id or tools.current_reply_to_id.get(), str(chat_id))
        from utils import matches_filter
        if not matches_filter(text, config.KEYBOARD_BUTTON_WHITELIST, config.KEYBOARD_BUTTON_BLACKLIST):
            return "Error: Outgoing message text is blocked by keyboard button configuration."

        edit_message_id = kwargs.pop("edit_message_id", None)
        if edit_message_id:
            result = await tools.client.edit_message(chat_id, int(edit_message_id), text, **kwargs)
            if tools.db:
                await tools.db.update_message_text(str(chat_id), int(edit_message_id), text)
            return f"Success. Message #{edit_message_id} edited. Content updated."

        if is_deleted_fallback and quote_text:
            clean_quote = quote_text.strip("[]")
            sender_link = f"[**{fallback_sender_name}**](tg://user?id={fallback_sender_id})" if fallback_sender_id else f"**{fallback_sender_name}**"
            formatted_quote = f"> {sender_link}\n"
            for line in clean_quote.split("\n"):
                formatted_quote += f"> {line}\n"
            final_text = f"{formatted_quote}\n{text}"
            result = await tools.client.send_message(chat_id, final_text, parse_mode="markdown")
            await tools.db.save_message(str(chat_id), "model", final_text, msg_id=result.id)
            import bot
            bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Message sent successfully with fallback quote. Message ID: {result.id}"

        if reply_to_msg_id and (not reply_to_chat_id or str(reply_to_chat_id) == str(chat_id)) and not quote_text:
            result = await tools.client.send_message(chat_id, text, reply_to=int(reply_to_msg_id), **kwargs)
        else:
            from telethon.tl.functions.messages import SendMessageRequest
            from telethon.tl.types import InputReplyToMessage
            
            reply_peer = None
            if reply_to_chat_id and str(reply_to_chat_id) != str(chat_id):
                try:
                    if isinstance(reply_to_chat_id, str):
                        try: reply_to_chat_id = int(reply_to_chat_id)
                        except ValueError: pass
                    reply_peer = await tools.client.get_input_entity(reply_to_chat_id)
                except Exception as peer_err:
                    logger.warning(f"Failed to get reply peer entity: {str(peer_err)}")

            reply_to_param = InputReplyToMessage(
                reply_to_msg_id=int(reply_to_msg_id) if reply_to_msg_id else None,
                reply_to_peer_id=reply_peer,
                quote_text=quote_text
            )
            peer_entity = await tools.client.get_input_entity(chat_id)
            request = SendMessageRequest(peer=peer_entity, message=text, reply_to=reply_to_param, **kwargs)
            result = await tools.client(request)

        sent_msg_id = None
        if hasattr(result, "id"):
            sent_msg_id = result.id
        elif hasattr(result, "updates"):
            for upd in result.updates:
                if hasattr(upd, "message") and hasattr(upd.message, "id"):
                    sent_msg_id = upd.message.id
                    break
                elif hasattr(upd, "id"):
                    sent_msg_id = upd.id
                    break

        if sent_msg_id:
            await tools.db.save_message(str(chat_id), "model", text, msg_id=sent_msg_id)
            import bot
            bot.processed_msg_ids.add((int(chat_id), sent_msg_id))
        else:
            await tools.db.save_message(str(chat_id), "model", text)

        cid = tools.current_chat_id.get()
        if str(chat_id) == str(cid):
            return (
                f"Success. Message delivered to current chat. Message ID: {result.id}.\n"
                f"[STRICT WARNING]: This message has been sent to the chat. Please leave response.text completely EMPTY "
                f"or call 'no_op_ignore' to finish the transaction without duplicate sending."
            )
        return f"Success. Message sent to chat {chat_id}. Message ID: {result.id}"

    async def execute_telegram_action(self, method_name: str, args_json: str, timeout: float = 60.0, wait_response_seconds: float = BOT_RESPONSE_TIMEOUT, **kwargs) -> str:
        """Calls helper asynchronous Telethon client methods or sends raw Telegram API requests."""
        from utils import matches_filter
        if not matches_filter(method_name, config.TELEGRAM_ACTION_WHITELIST, config.TELEGRAM_ACTION_BLACKLIST):
            return f"Calling method '{method_name}' is blocked by the security system."
        if not tools.client:
            return "Error: Telethon client is not initialized."

        try:
            raw_tl_request = kwargs.pop("raw_tl_request", None)
            if raw_tl_request:
                from telethon import functions
                tl_obj = eval(raw_tl_request)
                result = await asyncio.wait_for(tools.client(tl_obj), timeout=timeout)
                return f"Success. Raw TL-request completed: {str(result)[:500]}"

            call_kwargs = json.loads(args_json) if args_json else {}
            call_kwargs.pop("method_name", None)
            if kwargs:
                call_kwargs.update(kwargs)

            if method_name in ["send_message", "send_file"] and "entity" not in call_kwargs:
                try: call_kwargs["entity"] = tools.current_chat_id.get()
                except Exception: pass

            def resolve_sandbox_paths(data):
                if isinstance(data, dict):
                    return {k: resolve_sandbox_paths(v) for k, v in data.items()}
                elif isinstance(data, list):
                    return [resolve_sandbox_paths(v) for v in data]
                elif isinstance(data, str):
                    if len(data) < 255 and "/" not in data and "\\" not in data:
                        possible_file = WORKSPACE_DIR / data
                        if possible_file.exists() and possible_file.is_file():
                            return str(possible_file.resolve())
                return data

            call_kwargs = resolve_sandbox_paths(call_kwargs)

            if method_name in ["send_message", "send_file"] and "reply_to" not in call_kwargs:
                try:
                    target_id = None
                    entity = call_kwargs.get("entity")
                    if isinstance(entity, int): target_id = entity
                    else:
                        target_entity = await tools.client.get_entity(entity)
                        target_id = target_entity.id
                    cid = tools.current_chat_id.get()
                    if abs(int(target_id)) == abs(int(cid)):
                        call_kwargs["reply_to"] = tools.current_reply_to_id.get()
                except Exception as e:
                    logger.debug(f"Failed to substitute reply_to: {str(e)}")

            if method_name.startswith("functions."):
                async def auto_upload_files(data):
                    if isinstance(data, dict):
                        new_dict = {}
                        for k, v in data.items():
                            if isinstance(v, str) and os.path.isabs(v) and os.path.exists(v) and os.path.isfile(v):
                                uploaded_file_obj = await tools.client.upload_file(v)
                                new_dict[k] = uploaded_file_obj
                            else:
                                new_dict[k] = await auto_upload_files(v)
                        return new_dict
                    elif isinstance(data, list):
                        return [await auto_upload_files(item) for item in data]
                    return data
                call_kwargs = await auto_upload_files(call_kwargs)

            is_current_chat_send = False
            try:
                if method_name in ["send_message", "send_file"] and "entity" in call_kwargs:
                    target_id = None
                    entity = call_kwargs["entity"]
                    if isinstance(entity, int): target_id = entity
                    else:
                        target_entity = await tools.client.get_entity(entity)
                        target_id = target_entity.id
                    cid = tools.current_chat_id.get()
                    if abs(target_id) == abs(cid) or str(target_id) in str(cid) or str(cid) in str(target_id):
                        is_current_chat_send = True
            except Exception as check_ex:
                logger.debug(f"Duplicate check error: {str(check_ex)}")

            result = None
            if method_name.startswith("functions."):
                parts = method_name.split(".")[1:]
                import telethon.functions as tf
                obj = tf
                for part in parts:
                    obj = getattr(obj, part)
                request = obj(**call_kwargs)
                result = await asyncio.wait_for(tools.client(request), timeout=timeout)
            else:
                func = getattr(tools.client, method_name, None)
                if not func: return f"Method '{method_name}' not found."
                call_res = func(**call_kwargs)
                if inspect.isawaitable(call_res):
                    result = await asyncio.wait_for(call_res, timeout=timeout)
                else:
                    result = call_res

            if is_current_chat_send:
                return (
                    f"Success. Action {method_name} executed. Result: {str(result)[:500]}.\n"
                    f"[STRICT WARNING]: Do not duplicate this message text in response.text."
                )

            if method_name == "send_message" and "entity" in call_kwargs:
                entity = call_kwargs["entity"]
                is_target_bot = False
                try:
                    target_entity = await tools.client.get_entity(entity)
                    is_target_bot = getattr(target_entity, "bot", False)
                except Exception:
                    if isinstance(entity, str) and entity.lower().endswith("bot"):
                        is_target_bot = True

                if is_target_bot and hasattr(result, "id"):
                    sent_msg_id = result.id
                    for _ in range(int(wait_response_seconds)):
                        await asyncio.sleep(1.0)
                        try:
                            history = await tools.client.get_messages(entity, limit=1)
                            me = await tools.client.get_me()
                            if history and history[0].id > sent_msg_id and history[0].sender_id != me.id:
                                bot_reply = history[0]
                                reply_text = bot_reply.message or ""
                                buttons_text = []
                                if bot_reply.reply_markup and hasattr(bot_reply.reply_markup, 'rows'):
                                    for row in bot_reply.reply_markup.rows:
                                        row_btns = []
                                        for btn in row.buttons:
                                            btn_info = f"'{btn.text}'"
                                            if hasattr(btn, 'data') and btn.data:
                                                try: btn_info += f" (callback_data: '{btn.data.decode('utf-8')}')"
                                                except Exception: btn_info += f" (callback_hex: '{btn.data.hex()}')"
                                            elif hasattr(btn, 'url') and btn.url:
                                                btn_info += f" (url: '{btn.url}')"
                                            row_btns.append(btn_info)
                                        buttons_text.append(" | ".join(row_btns))
                                buttons_summary = ""
                                if buttons_text:
                                    buttons_summary = "\n[Inline buttons]:\n" + "\n".join(buttons_text)
                                return f"Delivered.\n--- Instant reply (Message ID: {bot_reply.id}) ---\nText: {reply_text}\n{buttons_summary}"
                        except Exception as hist_err:
                            logger.error(f"Error: {str(hist_err)}")
                    
            from utils import safe_serialize
            serialized_res = safe_serialize(result)
            truncated_res = serialized_res[:5000] + "\n[Output truncated]" if len(serialized_res) > 5000 else serialized_res
            return f"Action {method_name} successfully executed. Result: {truncated_res}"
        except Exception as e:
            return f"Error executing '{method_name}': {str(e)}"

    async def send_inline_bot_result(self, bot_username: str, query: str, result_index: int = DEFAULT_RESULT_INDEX, chat_id: str = None, **kwargs) -> str:
        """Performs an inline query to the specified external bot."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            interactive_mode = kwargs.pop("interactive_mode", False)
            results = await tools.client.inline_query(bot_username, query)
            if not results:
                return f"Inline bot @{bot_username} did not return any results for query '{query}'."
            if interactive_mode:
                from utils import safe_serialize
                out_results = []
                for idx, res in enumerate(results[:5]):
                    res_data = {"index": idx, "title": getattr(res, "title", "None"), "description": getattr(res, "description", "None"), "type": type(res).__name__}
                    out_results.append(res_data)
                return f"Interactive Results:\n{safe_serialize(out_results)}"
            if result_index < 0 or result_index >= len(results):
                return f"Result index {result_index} out of range (total: {len(results)})."
            reply_to_id = None
            try:
                cid = tools.current_chat_id.get()
                if abs(int(chat_id)) == abs(int(cid)):
                    reply_to_id = tools.current_reply_to_id.get()
            except Exception: pass
            await results[result_index].click(chat_id, reply_to=reply_to_id, **kwargs)
            return f"Result under index {result_index} from bot @{bot_username} sent to chat {chat_id}."
        except Exception as e:
            return f"Error executing inline query: {str(e)}"

    async def click_inline_button(self, chat_entity: str, message_id: int, button_index: int = None, button_text: str = None, timeout: float = BUTTON_CLICK_TIMEOUT, **kwargs) -> str:
        """Clicks on an inline button in the specified message of another bot."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_entity, str):
                try: chat_entity = int(chat_entity)
                except ValueError: pass
            message = await asyncio.wait_for(tools.client.get_messages(chat_entity, ids=message_id), timeout=timeout)
            if not message: return f"Message with ID {message_id} not found."
            if not message.reply_markup or not hasattr(message.reply_markup, 'rows'):
                return "There are no inline buttons."
            target_i, target_j = None, None
            found = False
            callback_data = kwargs.pop("callback_data", None)
            if callback_data:
                cb_bytes = bytes.fromhex(callback_data) if all(c in "0123456789abcdefABCDEF" for c in callback_data) else callback_data.encode('utf-8')
                for r_idx, row in enumerate(message.reply_markup.rows):
                    for b_idx, btn in enumerate(row.buttons):
                        if hasattr(btn, "data") and btn.data == cb_bytes:
                            target_i, target_j = r_idx, b_idx
                            found = True
                            break
                    if found: break
            if button_text is not None:
                for r_idx, row in enumerate(message.reply_markup.rows):
                    for b_idx, btn in enumerate(row.buttons):
                        if btn.text.strip().lower() == button_text.strip().lower():
                            target_i, target_j = r_idx, b_idx
                            found = True
                            break
                    if found: break
                if not found: return f"Button with text '{button_text}' not found."
            elif button_index is not None:
                idx = 0
                for r_idx, row in enumerate(message.reply_markup.rows):
                    for b_idx, btn in enumerate(row.buttons):
                        if idx == button_index:
                            target_i, target_j = r_idx, b_idx
                            found = True
                            break
                        idx += 1
                    if found: break
                if not found: return f"Button index {button_index} out of range."
            else:
                return "Specify button_index or button_text."
            
            target_btn = message.reply_markup.rows[target_i].buttons[target_j]
            # Resolve raw callback_data string of the button
            btn_cb_str = ""
            if target_btn and hasattr(target_btn, "data") and target_btn.data:
                try: btn_cb_str = target_btn.data.decode('utf-8')
                except Exception: btn_cb_str = target_btn.data.hex()
            from utils import matches_filter
            if btn_cb_str and not matches_filter(btn_cb_str, config.INLINE_CALLBACK_WHITELIST, config.INLINE_CALLBACK_BLACKLIST):
                return f"Error: Inline button callback_data '{btn_cb_str}' is blocked by configuration."
            await asyncio.wait_for(message.click(i=target_i, j=target_j, **kwargs), timeout=timeout)
            return f"Button successfully clicked at row {target_i}, col {target_j}."
        except Exception as e:
            return f"Error clicking button: {str(e)}"

    async def send_poll(self, question: str, options: List[str], chat_id: str = None, is_anonymous: bool = True, is_multiple_choice: bool = False, is_quiz: bool = False, correct_option_index: int = None, explanation: str = None, **kwargs) -> str:
        """Sends a native Telegram poll or quiz to the specified chat."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                question = await tools.ai_manager.executor.parse_execute_and_strip_tags(question, chat_id, None, str(chat_id))
                if explanation:
                    explanation = await tools.ai_manager.executor.parse_execute_and_strip_tags(explanation, chat_id, None, str(chat_id))
                parsed_options = []
                for opt in options:
                    opt_parsed = await tools.ai_manager.executor.parse_execute_and_strip_tags(opt, chat_id, None, str(chat_id))
                    parsed_options.append(opt_parsed)
                options = parsed_options

            from telethon.tl import types
            import random
            poll_answers = []
            for idx, opt in enumerate(options):
                poll_answers.append(types.PollAnswer(text=opt, option=str(idx).encode('utf-8')))
            correct_answers = []
            if is_quiz and correct_option_index is not None:
                correct_answers.append(str(correct_option_index).encode('utf-8'))
            poll_obj = types.Poll(
                id=random.randint(1, 10**12),
                question=question,
                answers=poll_answers,
                closed=False,
                public_voters=not is_anonymous,
                multiple_choice=is_multiple_choice if not is_quiz else False,
                quiz=is_quiz
            )
            media_poll = types.InputMediaPoll(
                poll=poll_obj,
                correct_answers=correct_answers if is_quiz else None,
                explanation=explanation if is_quiz else None
            )
            result = await tools.client.send_file(chat_id, media_poll, **kwargs)
            if tools.db:
                poll_info_str = f"[Poll: '{question}' | Options: {', '.join(options)}]"
                await tools.db.save_message(str(chat_id), "model", poll_info_str, msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Poll successfully sent. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending Telegram poll: {str(e)}"

    async def set_message_reaction(self, chat_id: str, message_id: int, reaction_emojis: str = None, action: str = "set", **kwargs) -> str:
        """
        Manages reactions on a specific message. Supports single/multiple emojis, Premium custom emojis, and full/partial clearing.
        
        Args:
            chat_id: Target chat ID or username.
            message_id: ID of the message to react to.
            reaction_emojis: Comma-separated list of emojis or custom emoji document IDs (e.g., "👍,❤️" or "5432112345,🔥"). Max 3 for Premium, 1 for non-Premium user..
            action: The action to perform: "set" (replace all with these), "add" (append to existing), "remove" (remove these specific ones), or "clear" (remove all).
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl import types as tl_types
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            
            action = action.lower().strip()
            
            def parse_emojis(em_str):
                res = []
                if not em_str:
                    return res
                parts = [e.strip() for e in str(em_str).split(",") if e.strip()]
                for part in parts:
                    if part.isdigit():
                        res.append(tl_types.ReactionCustomEmoji(document_id=int(part)))
                    else:
                        res.append(tl_types.ReactionEmoji(emoticon=part))
                return res
            
            input_reactions = parse_emojis(reaction_emojis)
            
            if action == "clear":
                reaction_list = []
            elif action == "set":
                reaction_list = input_reactions[:3]
            else:
                msg = await tools.client.get_messages(chat_id, ids=int(message_id))
                current_own = []
                if msg and msg.reactions and hasattr(msg.reactions, "results"):
                    for r_count in msg.reactions.results:
                        if getattr(r_count, "chosen", False):
                            current_own.append(r_count.reaction)
                
                if action == "add":
                    merged = list(current_own)
                    for r in input_reactions:
                        exists = False
                        for existing in merged:
                            if type(r) == type(existing):
                                if isinstance(r, tl_types.ReactionEmoji) and r.emoticon == existing.emoticon:
                                    exists = True
                                elif isinstance(r, tl_types.ReactionCustomEmoji) and r.document_id == existing.document_id:
                                    exists = True
                        if not exists:
                            merged.append(r)
                    reaction_list = merged[:3]
                elif action == "remove":
                    filtered = []
                    for existing in current_own:
                        to_remove = False
                        for r in input_reactions:
                            if type(r) == type(existing):
                                if isinstance(r, tl_types.ReactionEmoji) and r.emoticon == existing.emoticon:
                                    to_remove = True
                                elif isinstance(r, tl_types.ReactionCustomEmoji) and r.document_id == existing.document_id:
                                    to_remove = True
                        if not to_remove:
                            filtered.append(existing)
                    reaction_list = filtered[:3]
                else:
                    return f"Error: Invalid action '{action}'. Choose from 'set', 'add', 'remove', 'clear'."
            
            await tools.client(SendReactionRequest(peer=chat_id, msg_id=int(message_id), reaction=reaction_list))
            return f"Success! Message #{message_id} reactions updated via action '{action}'."
        except Exception as e:
            return f"Error setting reaction: {str(e)}"

    async def send_telegram_media(self, chat_id: str, media_id: str, access_hash: str, file_reference_hex: str, media_type: str, caption: str = None, reply_to_msg_id: int = None, **kwargs) -> str:
        """Sends any cached Telegram media using raw MTProto identification metadata."""
        from utils import matches_filter
        if not matches_filter(media_id, config.OUTGOING_FILE_WHITELIST, config.OUTGOING_FILE_BLACKLIST):
            return f"Error: Media ID '{media_id}' is blocked by configuration."
        if not matches_filter(file_reference_hex, config.OUTGOING_FILE_WHITELIST, config.OUTGOING_FILE_BLACKLIST):
            return f"Error: Media reference is blocked by configuration."
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            from telethon.tl import types as tl_types
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            file_ref_bytes = bytes.fromhex(file_reference_hex) if file_reference_hex and file_reference_hex != "none" else b""
            m_id = int(media_id)
            a_hash = int(access_hash)
            if media_type.lower() == 'photo':
                media_obj = tl_types.InputPhoto(id=m_id, access_hash=a_hash, file_reference=file_ref_bytes)
            else:
                media_obj = tl_types.InputDocument(id=m_id, access_hash=a_hash, file_reference=file_ref_bytes)
            reply_to_param = None
            if reply_to_msg_id:
                from telethon.tl.types import InputReplyToMessage
                reply_to_param = InputReplyToMessage(reply_to_msg_id=int(reply_to_msg_id))
            result = await tools.client.send_file(chat_id, file=media_obj, caption=caption, reply_to=reply_to_param)
            return f"Success. Media sent successfully. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending media: {str(e)}"

    async def send_media_message(self, chat_id: str = None, files: List[str] = None, caption: str = None, reply_to_msg_id: int = None, **kwargs) -> str:
        """Sends one or multiple media files (photos, videos, audio, documents, GIFs) to the specified chat."""
        if not tools.client: return "Error: Telethon client is not initialized."
        if not files: return "Error: Please specify files."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            resolved_files = []
            from utils import matches_filter
            for f in files:
                if not matches_filter(f, config.OUTGOING_FILE_WHITELIST, config.OUTGOING_FILE_BLACKLIST):
                    return f"Error: File '{f}' is blocked by configuration."
                f_path = WORKSPACE_DIR / os.path.basename(f)
                if f_path.exists(): resolved_files.append(str(f_path.resolve()))
                else: return f"Error: File '{f}' not found."
            target_reply_to = reply_to_msg_id or tools.current_reply_to_id.get()
            if caption and tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                caption = await tools.ai_manager.executor.parse_execute_and_strip_tags(caption, chat_id, target_reply_to, str(chat_id))
            file_arg = resolved_files[0] if len(resolved_files) == 1 else resolved_files
            result = await tools.client.send_file(chat_id, file=file_arg, caption=caption, reply_to=target_reply_to, parse_mode="html", **kwargs)
            msg_id = getattr(result, "id", None)
            if msg_id:
                media_info = json.dumps({"path": resolved_files[0], "mime_type": "media"})
                await tools.db.save_message(str(chat_id), "model", caption or "[Sent Media]", media_info=media_info, msg_id=msg_id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), msg_id))
            return "Success. Media message sent."
        except Exception as e:
            return f"Error: {str(e)}"

    async def edit_message(self, chat_id: str, message_id: int, new_text: str, **kwargs) -> str:
        """Edits a previously sent own message."""
        if not tools.client: return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            from utils import safe_telegram_html
            formatted_text = safe_telegram_html(new_text)
            await tools.client.edit_message(chat_id, int(message_id), formatted_text, parse_mode="html", **kwargs)
            if tools.db:
                await tools.db.update_message_text(str(chat_id), int(message_id), formatted_text)
            return f"Success. Message #{message_id} edited."
        except Exception as e:
            return f"Error: {str(e)}"

    async def delete_message(self, chat_id: str, message_id: int, **kwargs) -> str:
        """Deletes a message."""
        if not tools.client: return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            await tools.client.delete_messages(chat_id, [int(message_id)], **kwargs)
            if tools.db:
                await tools.db.update_message_text(str(chat_id), int(message_id), "[Message deleted]")
            return f"Success. Message #{message_id} deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    async def update_avatar(self, chat_id: str = None, filename: str = None, **kwargs) -> str:
        """Updates the profile photo (avatar) of the userbot itself or a specified chat/group/channel."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if not filename:
            return "Error: Please specify the filename."
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            if not file_path.exists():
                return f"Error: File '{filename}' not found."
            
            uploaded_file = await tools.client.upload_file(str(file_path.resolve()))
            if chat_id is None or str(chat_id).lower() == "me":
                from telethon.tl.functions.photos import UploadProfilePhotoRequest
                await tools.client(UploadProfilePhotoRequest(fallback=False, file=uploaded_file))
                return "Success. Userbot's own avatar updated."
            else:
                if isinstance(chat_id, str):
                    try: chat_id = int(chat_id)
                    except ValueError: pass
                from telethon.tl.functions.channels import EditChatPhotoRequest
                from telethon.tl.types import InputChatUploadedPhoto
                entity = await tools.client.get_input_entity(chat_id)
                try:
                    await tools.client(EditChatPhotoRequest(channel=entity, photo=InputChatUploadedPhoto(file=uploaded_file)))
                    return f"Success. Avatar updated for channel/group {chat_id}."
                except Exception:
                    from telethon.tl.functions.messages import EditChatPhotoRequest as MsgEditChatPhotoRequest
                    await tools.client(MsgEditChatPhotoRequest(chat_id=entity.chat_id if hasattr(entity, 'chat_id') else chat_id, photo=InputChatUploadedPhoto(file=uploaded_file)))
                    return f"Success. Avatar updated for group {chat_id}."
        except Exception as e:
            return f"Error updating avatar: {str(e)}"

    async def kick_user(self, user_id: str, chat_id: str = None, **kwargs) -> str:
        """Kicks a user from a group/channel, or blocks them in PM (with history clearing)."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        if isinstance(user_id, str):
            try: user_id = int(user_id)
            except ValueError: pass
        try:
            is_private = isinstance(chat_id, int) and chat_id > 0
            if is_private or str(chat_id) == str(user_id):
                from telethon.tl.functions.contacts import BlockRequest
                from telethon.tl.functions.messages import DeleteHistoryRequest
                await tools.client(BlockRequest(id=user_id))
                await tools.client(DeleteHistoryRequest(peer=user_id, max_id=0, just_clear=False, revoke=True))
                return "Success. User blocked and private history deleted."
            else:
                from telethon.tl.functions.channels import EditBannedRequest
                from telethon.tl.types import ChatBannedRights
                await tools.client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=ChatBannedRights(until_date=None, view_messages=True)))
                return f"Success. User kicked from chat {chat_id}."
        except Exception as e:
            return f"Error kicking user: {str(e)}"

    async def mute_user(self, user_id: str, chat_id: str = None, mute_type: str = "messages", duration_seconds: int = None, **kwargs) -> str:
        """Mutes a user (sound, notifications, or message sending restrictions) in the chat."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        if isinstance(user_id, str):
            try: user_id = int(user_id)
            except ValueError: pass
        try:
            import time
            until_date = int(time.time()) + duration_seconds if duration_seconds else None
            is_private = isinstance(chat_id, int) and chat_id > 0
            if is_private or str(chat_id) == str(user_id):
                return "Success. Notifications sound disabled for PM."
            else:
                from telethon.tl.types import ChatBannedRights
                from telethon.tl.functions.channels import EditBannedRequest
                rights = ChatBannedRights(
                    until_date=until_date,
                    send_messages=True if mute_type == "messages" else False,
                    send_media=True if mute_type in ["messages", "media"] else False,
                    send_stickers=True,
                    send_gifs=True,
                    send_games=True,
                    send_inline=True,
                    embed_links=True
                )
                await tools.client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=rights))
                return f"Success. User restricted in chat {chat_id}."
        except Exception as e:
            return f"Error muting user: {str(e)}"

    async def ban_user(self, user_id: str, chat_id: str = None, duration_seconds: int = None, **kwargs) -> str:
        """Bans a user from a group/channel, or blocks them in PM."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            if chat_id is None or chat_id == OWNER_ID:
                from telethon.tl.functions.contacts import BlockRequest
                await tools.client(BlockRequest(id=user_id))
                return f"Success. User {user_id} blocked."
            else:
                from telethon.tl.functions.channels import EditBannedRequest
                from telethon.tl.types import ChatBannedRights
                import time
                until = int(time.time()) + duration_seconds if duration_seconds else None
                await tools.client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=ChatBannedRights(until_date=until, view_messages=True)))
                return f"Success. User banned in chat {chat_id}."
        except Exception as e:
            return f"Error banning user: {str(e)}"

    async def unrestrict_user(self, user_id: str, chat_id: str = None, **kwargs) -> str:
        """Instantly lifts all bans, mutes, and restrictions from a user."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass
            if chat_id is None:
                from telethon.tl.functions.contacts import UnblockRequest
                await tools.client(UnblockRequest(id=user_id))
                return "Success. User unblocked in PM."
            else:
                from telethon.tl.functions.channels import EditBannedRequest
                from telethon.tl.types import ChatBannedRights
                await tools.client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=ChatBannedRights(until_date=0)))
                return f"Success. Message restrictions removed."
        except Exception as e:
            return f"Error removing restrictions: {str(e)}"

    async def click_keyboard_button(self, button_text: str, chat_entity: str = None, **kwargs) -> str:
        """Clicks on a normal reply keyboard button matching the provided text."""
        from utils import matches_filter
        if not matches_filter(button_text, config.KEYBOARD_BUTTON_WHITELIST, config.KEYBOARD_BUTTON_BLACKLIST):
            return f"Error: Keyboard button '{button_text}' is blocked by configuration."
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_entity is None:
            try: chat_entity = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine chat."
        if isinstance(chat_entity, str):
            try: chat_entity = int(chat_entity)
            except ValueError: pass
        try:
            history = await tools.client.get_messages(chat_entity, limit=config.TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT)
            target_btn = None
            for msg in history:
                if msg.reply_markup and hasattr(msg.reply_markup, 'rows'):
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            clean_btn_text = re.sub(r'^[^\w\s]+\s*', '', btn.text).strip().lower()
                            clean_req_text = re.sub(r'^[^\w\s]+\s*', '', button_text).strip().lower()
                            if clean_btn_text == clean_req_text or btn.text.strip().lower() == button_text.strip().lower():
                                target_btn = btn
                                break
                        if target_btn: break
                if target_btn: break
            if not target_btn:
                return f"Error: Keyboard button '{button_text}' not found."
            if hasattr(target_btn, "text"):
                await tools.client.send_message(chat_entity, target_btn.text, **kwargs)
                return f"Success. Clicked reply button '{target_btn.text}'."
            return "Error: Button type not supported."
        except Exception as e:
            return f"Error clicking reply keyboard button: {str(e)}"

    async def get_bot_commands(self, chat_entity: str, **kwargs) -> str:
        """Retrieves the full list of bot commands and hints in the active chat."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_entity, str):
                try: chat_entity = int(chat_entity)
                except ValueError: pass
            from telethon.tl.functions.bots import GetBotCommandsRequest
            result = await tools.client(GetBotCommandsRequest(scope=None, lang_code=""))
            lines = [f"/{cmd.command} - {cmd.description}" for cmd in result]
            return "\n".join(lines) if lines else "No commands found."
        except Exception as e:
            return f"Error getting commands: {str(e)}"

    async def send_bot_command(self, bot_username: str, command: str, payload: str = None, chat_id: str = None, **kwargs) -> str:
        """Sends a command to a bot with an optional payload."""
        from utils import matches_filter
        if not matches_filter(command, config.BOT_COMMAND_WHITELIST, config.BOT_COMMAND_BLACKLIST):
            return "Error: This bot command is blocked by configuration."
        if not tools.client:
            return "Error: Telethon client is not initialized."
        try:
            final_command = command
            if payload:
                final_command = f"{command} {payload}"
            await tools.client.send_message(bot_username, final_command, **kwargs)
            return f"Success. Command '{command}' sent."
        except Exception as e:
            return f"Error sending bot command: {str(e)}"

    async def join_telegram_chat(self, link_or_username: str, **kwargs) -> str:
        """
        Enables the userbot to join any public group, subscribe to a channel, 
        or send a join request to a private group/channel requiring admin approval.
        Handles both public links/usernames and private invite hashes automatically.

        Args:
            link_or_username: The t.me link, private invite link, hash, or public username (e.g., '@my_group', 'https://t.me/+AbCd123', 'joinchat/AAAAAF...').
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        import re
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        from telethon.errors import (
            InviteHashExpiredError, InviteHashInvalidError, 
            UserAlreadyParticipantError
        )
        clean_input = link_or_username.strip()
        hash_match = re.search(r'(?:t\.me|telegram\.me)/(?:joinchat/|\+)([a-zA-Z0-9_-]+)', clean_input, re.IGNORECASE)
        is_private_invite = False
        invite_hash = ""
        if hash_match:
            is_private_invite = True
            invite_hash = hash_match.group(1)
        elif clean_input.startswith("+") and len(clean_input) > 2:
            is_private_invite = True
            invite_hash = clean_input[1:]
        elif "joinchat/" in clean_input:
            is_private_invite = True
            invite_hash = clean_input.split("joinchat/")[-1].strip()
        elif not clean_input.startswith("@") and not "/" in clean_input and len(clean_input) >= 10 and clean_input.isalnum():
            is_private_invite = True
            invite_hash = clean_input
        try:
            if is_private_invite:
                logger.info(f"Private invite detected. Attempting ImportChatInviteRequest with hash: {invite_hash}...")
                try:
                    result = await tools.client(ImportChatInviteRequest(hash=invite_hash))
                    chat_title = "Private Chat"
                    if hasattr(result, "chats") and result.chats:
                        chat_title = getattr(result.chats[0], "title", "Private Group/Channel")
                    return f"Success! Successfully joined the private chat/channel '{chat_title}' using the invite link."
                except UserAlreadyParticipantError:
                    return "Info: You are already a participant of this private chat or channel."
                except InviteHashExpiredError:
                    return "Error: The invite link has expired or is no longer valid."
                except InviteHashInvalidError:
                    return "Error: The provided invite link or hash is malformed or invalid."
                except Exception as ex_join:
                    ex_str = str(ex_join).lower()
                    if "request" in ex_str and "sent" in ex_str or "approval" in ex_str:
                        return "Success! A request to join this private group has been successfully sent to the administrators. You will join as soon as they approve it."
                    raise ex_join
            else:
                username = clean_input
                if "t.me/" in username:
                    username = username.split("t.me/")[-1].split("?")[0].split("/")[0].strip()
                if not username.startswith("@") and not username.isdigit():
                    username = f"@{username}"
                logger.info(f"Public entity detected. Attempting JoinChannelRequest for: {username}...")
                entity = await tools.client.get_entity(username)
                await tools.client(JoinChannelRequest(channel=entity))
                chat_title = getattr(entity, "title", username)
                return f"Success! Successfully joined the public chat/channel '{chat_title}' (@{getattr(entity, 'username', '') or username})."
        except Exception as e:
            return f"Error joining Telegram chat/channel: {str(e)}"

    async def send_game_emoji(self, emoji: str, chat_id: str = None, **kwargs) -> str:
        """Sends a game emoji (dice, dart, bowling, basketball, football, slot machine)."""
        from utils import matches_filter
        if not matches_filter(emoji, config.GAME_EMOJI_WHITELIST, config.GAME_EMOJI_BLACKLIST):
            return f"Error: Game emoji '{emoji}' is blocked by configuration."
        if not tools.client: return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            from telethon.tl import types as tl_types
            edit_message_id = kwargs.pop("edit_message_id", None)
            if edit_message_id:
                await tools.client.edit_message(chat_id, int(edit_message_id), text=emoji, **kwargs)
                return f"Success. Message #{edit_message_id} edited with game emoji '{emoji}'."
            result = await tools.client.send_message(chat_id, file=tl_types.InputMediaDice(emoticon=emoji), **kwargs)
            val = getattr(result.media, "value", None)
            if tools.db:
                await tools.db.save_message(str(chat_id), "model", f"[Sent Game Emoji: {emoji} | Value: {val}]", msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Sent game emoji '{emoji}'. Value: {val}. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending game emoji: {str(e)}"

    async def send_geolocation(self, latitude: float, longitude: float, chat_id: str = None, period: int = None, **kwargs) -> str:
        """Sends a static geolocation point or shares a real-time live location."""
        if not tools.client: return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            from telethon.tl import types as tl_types
            if period:
                media_geo = tl_types.InputMediaGeoLive(geo_point=tl_types.InputGeoPoint(latitude=latitude, longitude=longitude), period=int(period))
            else:
                media_geo = tl_types.InputMediaGeoPoint(geo_point=tl_types.InputGeoPoint(latitude=latitude, longitude=longitude))
            edit_message_id = kwargs.pop("edit_message_id", None)
            if edit_message_id:
                await tools.client.edit_message(chat_id, int(edit_message_id), file=media_geo, **kwargs)
                return "Success. Message edited with geolocation."
            result = await tools.client.send_file(chat_id, media_geo, **kwargs)
            if tools.db:
                await tools.db.save_message(str(chat_id), "model", f"[Sent Location: {latitude}, {longitude}]", msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Geolocation sent. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending geolocation: {str(e)}"

    async def send_premium_list(self, title: str, items: List[str], chat_id: str = None, **kwargs) -> str:
        """Sends a beautifully formatted checklist / premium to-do list with custom checkboxes."""
        if not tools.client: return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                title = await tools.ai_manager.executor.parse_execute_and_strip_tags(title, chat_id, None, str(chat_id))
            body_parts = [f"<b>{title}</b>\n"]
            for item in items:
                if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                    item = await tools.ai_manager.executor.parse_execute_and_strip_tags(item, chat_id, None, str(chat_id))
                body_parts.append(f"☑️ {item}")
            final_text = "\n".join(body_parts)
            edit_message_id = kwargs.pop("edit_message_id", None)
            if edit_message_id:
                await tools.client.edit_message(chat_id, int(edit_message_id), final_text, parse_mode="html", **kwargs)
                return "Success. Checklist edited."
            result = await tools.client.send_message(chat_id, final_text, parse_mode="html", **kwargs)
            if tools.db:
                await tools.db.save_message(str(chat_id), "model", final_text, msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Checklist sent. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending list: {str(e)}"

    async def send_audio_music(self, filename: str, chat_id: str = None, caption: str = None, title: str = None, performer: str = None, **kwargs) -> str:
        """Sends an audio/music file with explicit track title and performer metadata."""
        if not tools.client: return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            if not file_path.exists(): return f"Error: File '{filename}' not found."
            if caption and tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                caption = await tools.ai_manager.executor.parse_execute_and_strip_tags(caption, chat_id, None, str(chat_id))
            from telethon.tl.types import DocumentAttributeAudio
            attributes = [DocumentAttributeAudio(duration=0, title=title or "Track", performer=performer or "Artist")]
            edit_message_id = kwargs.pop("edit_message_id", None)
            if edit_message_id:
                await tools.client.edit_message(chat_id, int(edit_message_id), file=str(file_path.resolve()), text=caption, attributes=attributes, **kwargs)
                return "Success. Audio edited."
            result = await tools.client.send_file(chat_id, str(file_path.resolve()), caption=caption, attributes=attributes, **kwargs)
            if tools.db:
                await tools.db.save_message(str(chat_id), "model", caption or f"[Sent Music: {title} by {performer}]", msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Audio sent. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending audio: {str(e)}"

    async def get_group_members(self, chat_id: str = None, only_admins: bool = False, limit: int = 100, **kwargs) -> str:
        """
        Retrieves the list of active members of the specified group or channel.
        Identifies if a member is an admin, owner, or regular participant, 
        along with their custom member tags (titles) if set.

        Args:
            chat_id: Target group/channel username or ID. Defaults to the current active chat.
            only_admins: If True, retrieves only administrative members. Default is False.
            limit: Maximum number of members to fetch. Default is 100.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            from telethon.tl.types import ChannelParticipantsAdmins, ChannelParticipantsRecent
            entity = await tools.client.get_input_entity(chat_id)
            p_filter = ChannelParticipantsAdmins() if only_admins else ChannelParticipantsRecent()
            logger.info(f"Fetching up to {limit} participants for chat {chat_id}...")
            participants = await tools.client.get_participants(entity, limit=limit, filter=p_filter)
            if not participants:
                return "No participants found or member list is restricted by the group settings."
            lines = []
            for p in participants:
                name = f"{getattr(p, 'first_name', '') or ''} {getattr(p, 'last_name', '') or ''}".strip() or "User"
                username_ref = f" (@{p.username})" if getattr(p, "username", None) else ""
                custom_title = "None"
                p_role = "Member"
                try:
                    permissions = await tools.client.get_permissions(chat_id, p)
                    if getattr(permissions, "is_admin", False): p_role = "Admin"
                    if getattr(permissions, "is_creator", False): p_role = "Owner/Creator"
                    custom_title = getattr(p, "rank", None) or "None"
                    if custom_title == "None":
                        from telethon.tl.functions.channels import GetParticipantRequest
                        res = await tools.client(GetParticipantRequest(channel=chat_id, participant=p))
                        custom_title = getattr(res.participant, "rank", None) or "None"
                except Exception:
                    pass
                lines.append(f"- ID: {p.id} | Name: '{name}'{username_ref} | Role: {p_role} | Member Tag/Title: '{custom_title}'")
            header_status = "Administrators List" if only_admins else "Recent Members List"
            return f"=== {header_status} (Count: {len(lines)}) ===\n" + "\n".join(lines)
        except Exception as e:
            return f"Error retrieving group members: {str(e)}"

    async def edit_chat_participant_settings(self, chat_id: str = None, user_id: str = "me", custom_title: str = None, is_anonymous: bool = None, **kwargs) -> str:
        """
        Modifies a participant's administrative settings (permissions, custom title/member tag, 
        or anonymity) in the specified chat or channel. Defaults to own account and current active chat.

        Args:
            chat_id: Target group/channel username or ID. Defaults to the current active chat.
            user_id: The username or numerical ID of the target user to modify. Defaults to 'me' (own userbot account).
            custom_title: The new custom title / member tag to set (max 16 characters).
            is_anonymous: If True, hides the administrator in the member list and posts anonymously. Only applicable to admins.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        if isinstance(user_id, str):
            try: user_id = int(user_id)
            except ValueError: pass
        try:
            from telethon.tl.functions.channels import EditAdminRequest
            from telethon.tl.types import ChatAdminRights
            target_user = "me" if str(user_id).lower() == "me" else user_id
            user_entity = await tools.client.get_input_entity(target_user)
            permissions = await tools.client.get_permissions(chat_id, user_entity)
            anon_val = is_anonymous if is_anonymous is not None else getattr(permissions, "anonymous", False)
            rights = ChatAdminRights(
                change_info=getattr(permissions, "change_info", True),
                post_messages=getattr(permissions, "post_messages", True),
                edit_messages=getattr(permissions, "edit_messages", True),
                delete_messages=getattr(permissions, "delete_messages", True),
                ban_users=getattr(permissions, "ban_users", True),
                invite_users=getattr(permissions, "invite_users", True),
                pin_messages=getattr(permissions, "pin_messages", True),
                add_admins=getattr(permissions, "add_admins", False),
                anonymous=anon_val,
                manage_call=getattr(permissions, "manage_call", True)
            )
            title_val = custom_title if custom_title is not None else getattr(permissions, "custom_title", "") or ""
            logger.info(f"Modifying participant settings for user {user_id} in chat {chat_id}...")
            await tools.client(EditAdminRequest(channel=chat_id, user_id=user_entity, admin_rights=rights, rank=title_val))
            return f"Success! Participant settings for '{user_id}' in chat {chat_id} updated. Custom Title: '{title_val}', Anonymous: {anon_val}."
        except Exception as e:
            return f"Error modifying chat participant settings: {str(e)}"

    async def get_chat_participant_info(self, chat_id: str = None, user_id: str = "me", **kwargs) -> str:
        """
        Retrieves detailed participant information, rights, and membership status 
        of a user relative to a specific chat or channel. Defaults to own account and current active chat.

        Args:
            chat_id: Target group/channel username or ID. Defaults to the current active chat.
            user_id: The username or numerical ID of the target user. Defaults to 'me' (own account).
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        if isinstance(user_id, str):
            try: user_id = int(user_id)
            except ValueError: pass
        try:
            target_user = "me" if str(user_id).lower() == "me" else user_id
            user_entity = await tools.client.get_input_entity(target_user)
            logger.info(f"Retrieving participant permissions for user {user_id} in chat {chat_id}...")
            permissions = await tools.client.get_permissions(chat_id, user_entity)
            from telethon.tl.functions.channels import GetParticipantRequest
            res = await tools.client(GetParticipantRequest(channel=chat_id, participant=user_entity))
            raw_participant = res.participant
            custom_title = getattr(raw_participant, "rank", None) or "None"
            status_list = []
            if getattr(permissions, "is_creator", False): status_list.append("Creator/Owner")
            if getattr(permissions, "is_admin", False): status_list.append("Administrator")
            if getattr(permissions, "participant", False): status_list.append("Regular Member")
            status_str = " | ".join(status_list) if status_list else "Not a participant (Left / Banned / External)"
            details = [
                f"Chat Participant Info for '{user_id}' in chat {chat_id}:",
                f"- Status: {status_str}",
                f"- Member Tag / Custom Title: '{custom_title}'"
            ]
            if getattr(permissions, "is_admin", False) or getattr(permissions, "is_creator", False):
                details.append("- Administrative Rights:")
                details.append(f"  * Can delete messages: {getattr(permissions, 'delete_messages', False)}")
                details.append(f"  * Can ban/restrict users: {getattr(permissions, 'ban_users', False)}")
                details.append(f"  * Can pin messages: {getattr(permissions, 'pin_messages', False)}")
                details.append(f"  * Can invite users: {getattr(permissions, 'invite_users', False)}")
                details.append(f"  * Can change group info: {getattr(permissions, 'change_info', False)}")
                details.append(f"  * Can manage voice calls: {getattr(permissions, 'manage_call', False)}")
                details.append(f"  * Posts anonymously: {getattr(permissions, 'anonymous', False)}")
            if getattr(permissions, "banned_rights", None):
                br = permissions.banned_rights
                details.append("- Restricted / Banned Rights (Member Restrictions):")
                details.append(f"  * Can send messages: {not getattr(br, 'send_messages', False)}")
                details.append(f"  * Can send media: {not getattr(br, 'send_media', False)}")
                details.append(f"  * Can send stickers/gifs: {not getattr(br, 'send_stickers', False)}")
                details.append(f"  * Can embed links: {not getattr(br, 'embed_links', False)}")
            return "\n".join(details)
        except Exception as e:
            return f"Error retrieving chat participant info: {str(e)}"

    async def update_account_info(self, first_name: str = None, last_name: str = None, about: str = None, username: str = None, avatar_filename: str = None, **kwargs) -> str:
        """
        Modifies any profile settings of the userbot account, including first name, 
        last name, biography (about), username, or profile picture (avatar).

        Args:
            first_name: Optional new first name for your Telegram account.
            last_name: Optional new last name for your Telegram account.
            about: Optional new biography/description (about me) for your profile.
            username: Optional new username (@username) for your account.
            avatar_filename: Optional local filename in the workspace to set as your profile avatar.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        from utils import matches_filter
        val_to_check = f"{first_name or ''} {last_name or ''} {username or ''} {avatar_filename or ''}".strip()
        if not matches_filter(val_to_check, config.ACCOUNT_SETTINGS_WHITELIST, config.ACCOUNT_SETTINGS_BLACKLIST):
            return "Error: Modifying account settings is blocked by configuration."
        try:
            from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
            res_parts = []
            if first_name is not None or last_name is not None or about is not None:
                me_obj = await tools.client.get_me()
                f_name = first_name if first_name is not None else me_obj.first_name
                l_name = last_name if last_name is not None else me_obj.last_name
                bio_val = about if about is not None else ""
                await tools.client(UpdateProfileRequest(first_name=f_name, last_name=l_name, about=bio_val, **kwargs))
                res_parts.append("Profile fields updated successfully.")
            if username is not None:
                await tools.client(UpdateUsernameRequest(username=username))
                res_parts.append(f"Username changed successfully to @{username}.")
            if avatar_filename is not None:
                avatar_res = await self.update_avatar(chat_id="me", filename=avatar_filename)
                res_parts.append(avatar_res)
            return "\n".join(res_parts) if res_parts else "No changes specified."
        except Exception as e:
            return f"Error updating account info: {str(e)}"

    async def edit_chat_settings(self, chat_id: str = None, title: str = None, description: str = None, **kwargs) -> str:
        """
        Modifies general settings (title, description/about) of the specified group, megagroup, or channel.
        Requires administrative permissions in the target chat.

        Args:
            chat_id: Target group/channel username or ID. Defaults to the current active chat.
            title: The new title for the group or channel.
            description: The new description/about text.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Could not resolve target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            from utils import matches_filter
            if title and not matches_filter(title, config.GROUP_SETTINGS_WHITELIST, config.GROUP_SETTINGS_BLACKLIST):
                return "Error: Group title is blocked by configuration."
            if description and not matches_filter(description, config.GROUP_SETTINGS_WHITELIST, config.GROUP_SETTINGS_BLACKLIST):
                return "Error: Group description is blocked by configuration."
                
            from telethon.tl.functions.channels import EditChatTitleRequest, EditChatAboutRequest
            if title:
                await tools.client(EditChatTitleRequest(channel=chat_id, title=title))
            if description:
                await tools.client(EditChatAboutRequest(peer=chat_id, about=description))
            return f"Success! Group settings updated. Title: {title}, Description: {description}."
        except Exception as e:
            return f"Error editing group settings: {str(e)}"

    async def manage_contact(self, action: str, phone: str = None, first_name: str = None, last_name: str = None, user_id: str = None, **kwargs) -> str:
        """
        Enables adding, editing, or deleting a contact in the userbot's contact list.

        Args:
            action: The action to perform ('add', 'edit', or 'delete').
            phone: The phone number of the contact (required for 'add' or 'edit' if user_id is missing).
            first_name: The first name of the contact.
            last_name: Optional last name of the contact.
            user_id: The numerical ID or username of the Telegram user (required for 'delete' or 'edit').
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        from utils import matches_filter
        val_to_check = phone or user_id or first_name or ""
        if not matches_filter(str(val_to_check), config.CONTACTS_MANAGE_WHITELIST, config.CONTACTS_MANAGE_BLACKLIST):
            return "Error: Contact management action is blocked by configuration."
        try:
            from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
            from telethon.tl.types import InputPhoneContact
            act = str(action).strip().lower()
            if act == "add" or (act == "edit" and not user_id):
                if not phone or not first_name:
                    return "Error: 'phone' and 'first_name' are required to add/edit a contact."
                clean_phone = phone.strip("+")
                contact_obj = InputPhoneContact(client_id=0, phone=clean_phone, first_name=first_name, last_name=last_name or "")
                result = await tools.client(ImportContactsRequest(contacts=[contact_obj]))
                if result.imported:
                    imported_user = result.users[0]
                    return f"Success! Contact '{first_name} {last_name or ''}' (ID: {imported_user.id}) successfully imported/saved."
                return "Error: Failed to import contact."
            elif act == "delete":
                if not user_id:
                    return "Error: 'user_id' is required to delete a contact."
                entity = await tools.client.get_input_entity(user_id)
                await tools.client(DeleteContactsRequest(id=[entity]))
                return f"Success! Contact '{user_id}' successfully deleted."
            elif act == "edit" and user_id:
                if not first_name:
                    return "Error: 'first_name' is required to edit a contact."
                user_obj = await tools.client.get_entity(user_id)
                target_phone = phone.strip("+") if phone else getattr(user_obj, "phone", "")
                if not target_phone:
                    return "Error: An existing contact's phone number must be specified."
                contact_obj = InputPhoneContact(client_id=0, phone=target_phone, first_name=first_name, last_name=last_name or "")
                await tools.client(ImportContactsRequest(contacts=[contact_obj]))
                return f"Success! Contact details for '{first_name} {last_name or ''}' updated."
            return f"Error: Invalid action '{action}'. Choose from 'add', 'edit', or 'delete'."
        except Exception as e:
            return f"Error managing contact: {str(e)}"

    async def pin_telegram_message(self, message_id: int, chat_id: str = None, notify: bool = False, **kwargs) -> str:
        """
        Pins a specific message in the specified chat. Requires pin privileges.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            await tools.client.pin_message(chat_id, int(message_id), notify=notify)
            return f"Success! Message #{message_id} pinned in chat {chat_id}."
        except Exception as e:
            return f"Error pinning message: {str(e)}"

    async def unpin_telegram_message(self, message_id: int = None, chat_id: str = None, **kwargs) -> str:
        """
        Unpins a specific message or all messages in the specified chat.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass
        try:
            await tools.client.unpin_message(chat_id, int(message_id) if message_id is not None else None)
            return f"Success! Pinned message(s) unpinned in chat {chat_id}."
        except Exception as e:
            return f"Error unpinning message: {str(e)}"

# Export methods to module level
toolkit_tg = AIToolKitTelegram()
for attr in dir(toolkit_tg):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_tg, attr)
