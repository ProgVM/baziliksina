# update.py
import os
import sys
import hashlib

def upgrade_parser():
    file_path = "parser.py"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    if "[Attached Media - Type:" in code or "File Reference (Hex):" in code:
        print("parser.py already updated.")
        return True

    start_marker = "async def parse_message_payload(client, db, message) -> str:"
    end_marker = "async def parse_reply_metadata("

    start_idx = code.find(start_marker)
    end_idx = code.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        print("Error: Could not locate parse_message_payload boundaries in parser.py.")
        return False

    new_payload_func = """async def parse_message_payload(client, db, message) -> str:
    \"\"\"
    Recursively analyzes the message, extracts and caches premium emojis,
    Star Gift animations, and structural attachment parameters, outputting complete raw metadata
    directly into the history so the multimodal AI can track and reuse specific IDs.
    \"\"\"
    meta_parts = []
    text = message.text or ""
    chat_id = str(message.chat_id)
    msg_id = message.id

    raw_meta_dict = {
        "to_dict_raw": message.to_dict() if hasattr(message, "to_dict") else {}
    }

    # 1. Parsing and caching Premium custom emojis in-place
    if message.entities:
        emoji_refs = []
        for ent in message.entities:
            if isinstance(ent, tl_types.MessageEntityCustomEmoji):
                doc_id = ent.document_id
                local_path = await get_cached_premium_emoji(client, doc_id, is_animated=False)
                ref_str = f"[Custom Premium Emoji ID: {doc_id} (Local path: {local_path or 'not downloaded'})]"
                emoji_refs.append(ref_str)
        if emoji_refs:
            meta_parts.append("\\n".join(emoji_refs))

    # 2. Parsing Star Gifts with animations
    if message.media and type(message.media).__name__ == "MessageMediaGift":
        gift = message.media
        gift_text = getattr(gift, "text", "") or ""
        sender_gift_id = getattr(gift, "from_id", "anonymously")
        gift_id = getattr(gift, "gift_id", None)
        local_gift_path = await get_cached_gift_animation(client, gift_id) if gift_id else None
        gift_ref = f"[Star Gift Received | ID: {gift_id or 'unknown'} | Sender: {sender_gift_id} | Text: '{gift_text}' | Animation path: '{local_gift_path or 'not downloaded'}']"
        meta_parts.append(gift_ref)

    # 3. Extract complete raw MTProto parameters for attached media
    media_desc = get_media_type_description(message)
    if media_desc:
        media_id = "unknown"
        access_hash = "unknown"
        file_ref_hex = "none"
        if hasattr(message.media, "document") and message.media.document:
            doc = message.media.document
            media_id = doc.id
            access_hash = doc.access_hash
            file_ref_hex = doc.file_reference.hex() if doc.file_reference else "none"
        elif hasattr(message.media, "photo") and message.media.photo:
            photo = message.media.photo
            media_id = photo.id
            access_hash = photo.access_hash
            file_ref_hex = photo.file_reference.hex() if photo.file_reference else "none"
        meta_parts.append(f"[Attached Media - Type: {media_desc} | ID: {media_id} | Access Hash: {access_hash} | File Reference (Hex): {file_ref_hex}]")

    # Save all visual/secondary message metadata in msgs_meta
    meta_text_block = "\\n".join(meta_parts).strip()
    if meta_text_block:
        await db.save_msg_meta(chat_id, msg_id, meta_text=meta_text_block, raw_meta_dict=raw_meta_dict)

    # If the message has no text but has media, return the media descriptor as fallback
    if not text and media_desc:
        return f"[{media_desc}]"

    return text

"""
    code = code[:start_idx] + new_payload_func + code[end_idx:]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("parser.py updated.")
    return True


def upgrade_bot():
    file_path = "bot.py"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    if "full_outgoing_text =" in code or "Match reply context metadata" in code:
        print("bot.py already updated.")
        return True

    old_block = """    # 2. Universal processing and detailed saving of ALL outgoing messages
    if event.sender_id == me.id:
        text = await parse_message_payload(client, db, event.message)
        logger.info(f"Recording outgoing message {msg_id} in chat {chat_id}: '{text[:100]}...'")
        
        # Download and cache any outgoing media files similarly to incoming ones
        media_info = await download_and_cache_media(client, event.message, is_private=True, mentioned=True)
        await db.save_message(str(chat_id), "model", text, media_info, msg_id)
        return"""

    new_block = """    # 2. Universal processing and detailed saving of ALL outgoing messages
    if event.sender_id == me.id:
        text = await parse_message_payload(client, db, event.message)
        
        # Match reply context metadata for outgoing messages
        reply_meta = ""
        if event.message.is_reply:
            reply_meta = await parse_reply_metadata(event.message, chat_id, client, db)
        full_outgoing_text = f"{reply_meta}{text}".strip()
        
        logger.info(f"Recording outgoing message {msg_id} in chat {chat_id}: '{full_outgoing_text[:100]}...'")
        media_info = await download_and_cache_media(client, event.message, is_private=True, mentioned=True)
        await db.save_message(str(chat_id), "model", full_outgoing_text, media_info, msg_id)
        return"""

    if old_block not in code:
        print("Error: Could not locate target outgoing block in bot.py.")
        return False

    code = code.replace(old_block, new_block)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("bot.py updated.")
    return True


def upgrade_gemini():
    file_path = "gemini_manager.py"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    if "PHYSICAL REPLY ROUTING" in code:
        print("gemini_manager.py already updated.")
        return True

    # 1. Update Section 5 in System Prompt (Adding Item 8: PHYSICAL REPLY ROUTING)
    label7 = "7. PRECISE TARGETING:"
    idx7 = code.find(label7)
    if idx7 == -1:
        print("Error: Could not locate '7. PRECISE TARGETING:' in gemini_manager.py.")
        return False
        
    idx_quote_end = code.find('\\n\\""', idx7)
    if idx_quote_end == -1:
        idx_quote_end = code.find('\\n"', idx7)
    if idx_quote_end == -1:
        idx_quote_end = code.find("\\n'", idx7)
        
    if idx_quote_end == -1:
        print("Error: Could not find the end of line 7 in gemini_manager.py.")
        return False

    old_line7 = code[idx7 : idx_quote_end + 4]
    new_line7 = (
        "7. PRECISE TARGETING: The default plain-text response (response.text) is automatically configured to safely reply to the original triggering message ID that initiated this generation transaction (even if new messages arrived in the meantime). However, if multiple user messages accumulated in your history context during your turns, or if you want to target a specific statement further up the thread, you should call the `send_agent_message` tool and specify the exact `reply_to_msg_id` of the message you are addressing. Be precise with your target selection to avoid confusing chat participants.\\n\"\n"
        "            f\"8. PHYSICAL REPLY ROUTING: Whenever you write a plain-text response (response.text) that is meant to reply to a specific user's message, you MUST start your response with `[Reply: MESSAGE_ID]` (e.g. `[Reply: 487657]\\\\nYour conversational response here`). This instructs the system to route your reply arrow exactly to that message. Always make sure to use the exact Message ID of the message you are contextually answering up the thread to prevent confusing chat participants!\\\\n\""
    )
    code = code.replace(old_line7, new_line7)

    # 2. Add [Reply: MSG_ID] text parser before sending outgoing message
    old_send_block = """                if response.text and not function_calls_to_execute and not should_ignore:
                    typing_task.cancel()
                    
                    # Programmatically strip any generated [Chat: ... | Message ID: ...] prefixes
                    cleaned_text = response.text
                    prefix_pattern = re.compile(r'^\[Chat:\\s*-?\\d+\\s*\\|\\s*Message ID:\\s*(?:\\d+|unknown)\]\\s*\\n?', re.IGNORECASE)
                    cleaned_text = prefix_pattern.sub("", cleaned_text).strip()"""

    new_send_block = """                if response.text and not function_calls_to_execute and not should_ignore:
                    typing_task.cancel()
                    
                    cleaned_text = response.text
                    
                    # Parse custom [Reply: MSG_ID] routing header if provided by the model
                    custom_reply_id = reply_to_id
                    reply_match = re.match(r'^\[Reply:\\s*(\\d+)\]\\s*\\n?', cleaned_text, re.IGNORECASE)
                    if reply_match:
                        try:
                            custom_reply_id = int(reply_match.group(1))
                            cleaned_text = cleaned_text[reply_match.end():].strip()
                            logger.info(f"Dynamic reply routing detected. Setting reply_to to message #{custom_reply_id}")
                        except Exception as e:
                            logger.warning(f"Failed to parse custom reply ID: {str(e)}")
                    
                    # Programmatically strip any generated [Chat: ... | Message ID: ...] or reply prefixes to avoid leakage
                    prefix_pattern = re.compile(
                        r'^(\\[Chat:\\s*-?\\d+\\s*\\|\\s*Message ID:\\s*(?:\\d+|unknown)\\]|'
                        r'\\[Reply to message\\s*#\\d+(?:\\s*in\\s*[^\\\\]+)?\\]|'
                        r'\\[Original text\\s*\\([^\\\\]+\\):\\s*.*?\\]|'
                        r'\\[Selected fragment\\s*/\\s*Quote\\]:\\s*\\".*?\\"|'
                        r'\\[Attached Media\\s*-.*?\\])\\s*\\n?', 
                        re.DOTALL | re.IGNORECASE
                    )
                    cleaned_text = prefix_pattern.sub("", cleaned_text).strip()"""

    if old_send_block in code:
        code = code.replace(old_send_block, new_send_block)
        # Replace client.send_message call to use custom_reply_id
        old_send_msg_line = "result = await self.client.send_message(chat_entity, cleaned_text, reply_to=reply_to_id)"
        new_send_msg_line = "result = await self.client.send_message(chat_entity, cleaned_text, reply_to=custom_reply_id)"
        code = code.replace(old_send_msg_line, new_send_msg_line)

    # 3. Add Self-Healing File Cache inside Turn media loop
    old_media_block = """                                    with open(m_path, "rb") as f:
                                        file_bytes = f.read()
                                    
                                    has_inline = False
                                    for part in (content_obj.parts or []):
                                        if part.inline_data:
                                            part.inline_data.data = file_bytes
                                            has_inline = True
                                            break
                                    if not has_inline:
                                        content_obj.parts.insert(0, types.Part.from_bytes(data=file_bytes, mime_type=m_type))
                                    media_count += 1"""

    new_media_block = """                                    is_image = m_type.startswith("image/")
                                    file_size = os.path.getsize(m_path)
                                    if is_image and file_size < 4 * 1024 * 1024:
                                        with open(m_path, "rb") as f:
                                            file_bytes = f.read()
                                        has_inline = False
                                        for part in (content_obj.parts or []):
                                            if part.inline_data:
                                                part.inline_data.data = file_bytes
                                                has_inline = True
                                                break
                                        if not has_inline:
                                            content_obj.parts.insert(0, types.Part.from_bytes(data=file_bytes, mime_type=m_type))
                                        media_count += 1
                                    else:
                                        file_hash = hashlib.md5(m_path.encode('utf-8')).hexdigest()
                                        cache_key = f"google_file_uri_{file_hash}"
                                        google_uri = await self.db.get_memory(cache_key)
                                        if google_uri:
                                            # Validate the cached file state before using it
                                            try:
                                                file_name = google_uri.split("/")[-1]
                                                file_info = await gemini_client.aio.files.get(name=file_name)
                                                if file_info.state.name == "PROCESSING":
                                                    from utils import wait_for_google_file_active
                                                    if not await wait_for_google_file_active(gemini_client, file_name):
                                                        google_uri = None
                                                elif file_info.state.name == "FAILED":
                                                    logger.warning(f"Cached Google file {google_uri} is FAILED. Evicting cache...")
                                                    await self.db.db.execute("DELETE FROM shared_memory WHERE key = ?", (cache_key,))
                                                    await self.db.db.commit()
                                                    google_uri = None
                                            except Exception as check_err:
                                                logger.warning(f"Cached Google file {google_uri} is inaccessible ({str(check_err)}). Evicting cache...")
                                                await self.db.db.execute("DELETE FROM shared_memory WHERE key = ?", (cache_key,))
                                                await self.db.db.commit()
                                                google_uri = None
                                        if not google_uri:
                                            try:
                                                logger.info(f"Uploading file '{m_path}' to Google Files API on the fly...")
                                                uploaded_file = await gemini_client.aio.files.upload(file=m_path)
                                                google_uri = uploaded_file.uri
                                                
                                                from utils import wait_for_google_file_active
                                                if await wait_for_google_file_active(gemini_client, uploaded_file.name):
                                                    await self.db.set_memory(cache_key, google_uri)
                                                    await self.db.set_memory(google_uri, m_type)
                                                    logger.info(f"File successfully uploaded and processed. URI: {google_uri}")
                                                else:
                                                    logger.warning(f"Google file processing failed or timed out for {m_path}.")
                                                    google_uri = None
                                            except Exception as upload_err:
                                                logger.error(f"On-the-fly Google upload failed for {m_path}: {str(upload_err)}")
                                                google_uri = None
                                        if google_uri:
                                            content_obj.parts.insert(0, types.Part.from_uri(file_uri=google_uri, mime_type=m_type))
                                            media_count += 1"""

    if old_media_block in code:
        code = code.replace(old_media_block, new_media_block)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("gemini_manager.py upgraded successfully.")
    return True


def verify_syntax():
    for f in ["parser.py", "bot.py", "gemini_manager.py"]:
        try:
            with open(f, "r", encoding="utf-8") as source:
                compile(source.read(), f, "exec")
            print(f"  {f}: OK")
        except Exception as e:
            print(f"  {f}: SYNTAX ERROR! {str(e)}")
            return False
    return True


if __name__ == "__main__":
    success = upgrade_parser()
    success = upgrade_bot() and success
    success = upgrade_gemini() and success
    
    if success:
        if verify_syntax():
            print("Status: Success")
        else:
            print("Status: Failed")
    else:
        print("Status: Failed")
