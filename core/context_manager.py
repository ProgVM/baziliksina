# core/context_manager.py
import os
import json
import logging
import re
import hashlib
import asyncio
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from google.genai import types
from google.genai.errors import APIError

import config
from utils import wait_for_google_file_active, matches_filter, is_gemini_supported_mime, detect_mime_type, get_file_content_hash, GEMINI_SUPPORTED_MIME_TYPES

logger = logging.getLogger("ContextManager")


class AIContextManager:
    """
    Manages complete dialogue history retrieval, dual-engine token limits calculation,
    multimodal asset binding, explicit file attachment policies, and strategy-driven context
    management (summarize, trim, hybrid, none).
    """
    def __init__(self, db_manager, key_manager):
        self.db = db_manager
        self.key_manager = key_manager

    async def summarize_chat_context(self, gemini_client, chat_id: str = "global"):
        """
        Compresses the cross-cutting history log or specific chat history using an externalized prompt.
        """
        logger.info(f"Summarizing chat context for target '{chat_id}'...")
        history_raw = await self.db.get_history(chat_id, limit=config.SUMMARIZATION_MESSAGES_LIMIT)
        
        prompt_path = config.BASE_DIR / "config" / "summarize_prompt.txt"
        if prompt_path.exists():
            try:
                with open(prompt_path, "r", encoding="utf-8") as f:
                    prompt = f.read().strip()
            except Exception as e:
                logger.error(f"Error reading summarize_prompt.txt: {str(e)}")
                prompt = "Provide a brief summary of the following chat history log."
        else:
            prompt = "Provide a brief summary of the following chat history log."

        contents = []
        for content_obj, _ in history_raw:
            text_parts = [p.text for p in (content_obj.parts or []) if p.text and p.text.strip()]
            if text_parts:
                contents.append(types.Content(
                    role=content_obj.role,
                    parts=[types.Part.from_text(text="\n".join(text_parts))]
                ))
            
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))
        
        try:
            response = await gemini_client.aio.models.generate_content(
                model=self.key_manager.get_model(),
                contents=contents
            )
            summary_text = response.text
            await self.db.update_summary(chat_id, summary_text)
            await self.db.clear_history_for_summarization(chat_id, keep_last_n=config.SUMMARIZATION_KEEP_LIMIT)
            logger.info(f"Context summarization for '{chat_id}' completed successfully.")
        except Exception as e:
            logger.error(f"Error during summarization for '{chat_id}': {str(e)}")

    async def generate_media_summary(self, gemini_client, file_path: str, mime_type: str) -> str:
        """
        Generates a concise text description of a media file and caches it in shared_memory.
        """
        file_hash = get_file_content_hash(file_path)
        summary_key = f"media_summary_{file_hash}"
        
        cached_summary = await self.db.get_memory(summary_key)
        if cached_summary:
            return cached_summary

        detected_mime = detect_mime_type(file_path, fallback_mime=mime_type)
        if not is_gemini_supported_mime(detected_mime):
            fallback_text = f"[Media Attachment: {os.path.basename(file_path)} ({detected_mime})]"
            await self.db.set_memory(summary_key, fallback_text)
            return fallback_text

        logger.info(f"Generating AI visual/audio summary for media file: {file_path}")
        try:
            file_size = os.path.getsize(file_path)
            if file_size < 4 * 1024 * 1024 and detected_mime.startswith("image/"):
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                file_part = types.Part.from_bytes(data=file_bytes, mime_type=detected_mime)
            else:
                try:
                    upload_cfg = types.UploadFileConfig(mime_type=detected_mime) if hasattr(types, "UploadFileConfig") else {"mime_type": detected_mime}
                    uploaded_file = await gemini_client.aio.files.upload(file=file_path, config=upload_cfg)
                except Exception:
                    uploaded_file = await gemini_client.aio.files.upload(file=file_path)
                if not await wait_for_google_file_active(gemini_client, uploaded_file.name):
                    return "[Media summary unavailable: file processing timeout]"
                file_mime = uploaded_file.mime_type if is_gemini_supported_mime(uploaded_file.mime_type) else detected_mime
                file_part = types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=file_mime)

            prompt_content = types.Content(
                role="user",
                parts=[
                    file_part,
                    types.Part.from_text(text="Provide a concise 1-2 sentence description of this media attachment for chat context history.")
                ]
            )
            response = await gemini_client.aio.models.generate_content(
                model=self.key_manager.get_model(),
                contents=[prompt_content]
            )
            summary_text = f"[Media Summary: {response.text.strip()}]" if response.text else "[Media Summary: File attached]"
            await self.db.set_memory(summary_key, summary_text)
            return summary_text
        except Exception as e:
            logger.error(f"Failed to generate media summary for {file_path}: {str(e)}")
            return f"[Media Attachment: {os.path.basename(file_path)} ({detected_mime})]"

    async def _heal_unsupported_mime(self, offending_mime: str, contents: list = None, chat_id: str = None):
        """
        Permanently sanitizes SQLite database and active session context 
        to remove parts with unsupported MIME types (such as application/octet-stream).
        """
        target_mime = str(offending_mime or "application/octet-stream").strip()
        logger.info(f"Sanitizing database and context from unsupported MIME type: '{target_mime}'...")
        try:
            # 1. Clean shared_memory from URIs and cache keys
            async with self.db.db.execute(
                "SELECT key, value FROM shared_memory WHERE value = ? OR value LIKE ? OR key LIKE ?", 
                (target_mime, f"%{target_mime}%", f"%{target_mime}%")
            ) as cursor:
                cache_rows = await cursor.fetchall()
            for key, val in cache_rows:
                await self.db.db.execute("DELETE FROM shared_memory WHERE key = ?", (key,))
                await self.db.db.execute("DELETE FROM shared_memory WHERE value = ?", (key,))

            # 2. Clean messages table raw_content_json across ALL chats
            async with self.db.db.execute(
                "SELECT id, raw_content_json FROM messages WHERE raw_content_json LIKE ?", 
                (f"%{target_mime}%",)
            ) as cursor:
                db_rows = await cursor.fetchall()
            
            for r_id, db_raw_json in db_rows:
                if not db_raw_json:
                    continue
                try:
                    data_obj = json.loads(db_raw_json)
                    if "parts" in data_obj and isinstance(data_obj["parts"], list):
                        new_parts = []
                        for p in data_obj["parts"]:
                            is_offending = False
                            if isinstance(p, dict):
                                if p.get("file_data") and (target_mime in str(p.get("file_data")) or not is_gemini_supported_mime(p.get("file_data", {}).get("mime_type"))):
                                    is_offending = True
                                elif p.get("inline_data") and (target_mime in str(p.get("inline_data")) or not is_gemini_supported_mime(p.get("inline_data", {}).get("mime_type"))):
                                    is_offending = True
                            if is_offending:
                                new_parts.append({"text": f"[System: File attachment with unsupported MIME '{target_mime}' omitted]"})
                            else:
                                new_parts.append(p)
                        data_obj["parts"] = new_parts
                        cleaned_json = json.dumps(data_obj, ensure_ascii=False)
                        await self.db.db.execute("UPDATE messages SET raw_content_json = ? WHERE id = ?", (cleaned_json, r_id))
                except Exception as json_err:
                    logger.error(f"Failed to clean message #{r_id} JSON: {str(json_err)}")

            # 3. Clean messages table media_info
            async with self.db.db.execute(
                "SELECT id, media_info FROM messages WHERE media_info LIKE ?", 
                (f"%{target_mime}%",)
            ) as cursor:
                media_rows = await cursor.fetchall()

            for m_id, m_info_str in media_rows:
                if not m_info_str:
                    continue
                try:
                    m_data = json.loads(m_info_str)
                    if isinstance(m_data, dict) and "items" in m_data:
                        m_data["items"] = [item for item in m_data["items"] if item.get("mime_type") != target_mime]
                        cleaned_m_info = json.dumps(m_data, ensure_ascii=False) if m_data["items"] else None
                    elif isinstance(m_data, dict) and m_data.get("mime_type") == target_mime:
                        cleaned_m_info = None
                    else:
                        cleaned_m_info = m_info_str
                    await self.db.db.execute("UPDATE messages SET media_info = ? WHERE id = ?", (cleaned_m_info, m_id))
                except Exception as m_err:
                    logger.error(f"Failed to clean media_info for #{m_id}: {str(m_err)}")

            await self.db.db.commit()
            logger.info(f"Database successfully sanitized from unsupported MIME type '{target_mime}'.")
        except Exception as db_err:
            logger.error(f"Error sanitizing database for MIME type '{target_mime}': {str(db_err)}")

        if contents:
            for content in contents:
                if content.parts:
                    new_parts = []
                    for part in content.parts:
                        is_offending = False
                        if hasattr(part, "file_data") and part.file_data:
                            fd_m = getattr(part.file_data, "mime_type", "")
                            if fd_m == target_mime or not is_gemini_supported_mime(fd_m):
                                is_offending = True
                        elif hasattr(part, "inline_data") and part.inline_data:
                            id_m = getattr(part.inline_data, "mime_type", "")
                            if id_m == target_mime or not is_gemini_supported_mime(id_m):
                                is_offending = True
                        if is_offending:
                            new_parts.append(types.Part.from_text(text=f"[System: File attachment with unsupported MIME '{target_mime}' omitted]"))
                        else:
                            new_parts.append(part)
                    content.parts = new_parts

    async def _heal_inaccessible_file(self, file_id: str, contents: list):
        """
        Permanently sanitizes SQLite database and active session context 
        to remove inaccessible Google File URIs after key rotations.
        """
        logger.info(f"Inaccessible File ID identified: {file_id}. Sanitizing database context...")
        try:
            async with self.db.db.execute(
                "SELECT id, text, raw_content_json FROM messages WHERE text LIKE ? OR raw_content_json LIKE ?", 
                (f"%{file_id}%", f"%{file_id}%")
            ) as cursor:
                db_rows = await cursor.fetchall()
            
            for r_id, db_text, db_raw_json in db_rows:
                cleaned_db_text = None
                if db_text:
                    cleaned_db_text = re.sub(
                        r"https://generativelanguage\.googleapis\.com/(?:upload/)?v1beta/files/" + re.escape(file_id),
                        "[File inaccessible due to API key rotation]",
                        db_text,
                        flags=re.IGNORECASE
                    )
                
                cleaned_db_json = None
                if db_raw_json:
                    cleaned_db_json = re.sub(
                        r"https://generativelanguage\.googleapis\.com/(?:upload/)?v1beta/files/" + re.escape(file_id),
                        "[File inaccessible due to API key rotation]",
                        db_raw_json,
                        flags=re.IGNORECASE
                    )
                    
                    try:
                        data_obj = json.loads(cleaned_db_json)
                        if "parts" in data_obj and isinstance(data_obj["parts"], list):
                            new_parts = []
                            for p in data_obj["parts"]:
                                is_offending = False
                                if isinstance(p, dict):
                                    if p.get("file_data") and (file_id in str(p.get("file_data")) or "[File inaccessible" in str(p.get("file_data"))):
                                        is_offending = True
                                    elif p.get("inline_data") and "[File inaccessible" in str(p.get("inline_data")):
                                        is_offending = True
                                        
                                if is_offending:
                                    new_parts.append({"text": "[System: File attachment inaccessible due to API key rotation]"})
                                else:
                                    new_parts.append(p)
                            data_obj["parts"] = new_parts
                            cleaned_db_json = json.dumps(data_obj)
                    except Exception as json_err:
                        logger.error(f"Failed to deeply reconstruct JSON for File ID {file_id}: {str(json_err)}")

                await self.db.db.execute(
                    "UPDATE messages SET text = ?, raw_content_json = ? WHERE id = ?", 
                    (cleaned_db_text if cleaned_db_text is not None else db_text, 
                     cleaned_db_json if cleaned_db_json is not None else db_raw_json, 
                     r_id)
                )

            try:
                async with self.db.db.execute(
                    "SELECT key, value FROM shared_memory WHERE value LIKE ?", 
                    (f"%{file_id}%",)
                ) as cursor:
                    cache_rows = await cursor.fetchall()
                for key, val in cache_rows:
                    await self.db.db.execute("DELETE FROM shared_memory WHERE key = ?", (key,))
                    await self.db.db.execute("DELETE FROM shared_memory WHERE key = ?", (val,))
            except Exception as cache_err:
                logger.error(f"Failed to clear shared_memory cache for {file_id}: {str(cache_err)}")

            await self.db.db.commit()
            logger.info(f"Permanently sanitized database row(s) containing File ID {file_id}.")
        except Exception as db_clean_err:
            logger.error(f"Failed to sanitize database for File ID {file_id}: {str(db_clean_err)}")
        
        for content in contents:
            if content.parts:
                new_parts = []
                for part in content.parts:
                    is_offending = False
                    if hasattr(part, "file_data") and part.file_data and hasattr(part.file_data, "file_uri") and part.file_data.file_uri:
                        if file_id in part.file_data.file_uri:
                            is_offending = True
                    
                    if is_offending:
                        new_parts.append(types.Part.from_text(text="[System: File attachment inaccessible due to API key rotation]"))
                    else:
                        new_parts.append(part)
                content.parts = new_parts

    async def get_aligned_history(self, chat_id: str, gemini_client, max_db_id: int = None) -> list:
        """
        Retrieves history from SQLite, applies context management strategies (summarize/trim/hybrid/none),
        evaluates explicit file attachment rules, strictly aligns and validates turn roles & function calling pairs,
        and prepares a 100% valid Gemini API contents payload.
        """
        text_mode = getattr(config, "CONTEXT_MANAGEMENT_MODE", "summarize").lower()
        file_mode = getattr(config, "FILE_CONTEXT_MODE", "trim").lower()
        auto_attach = getattr(config, "AUTO_ATTACH_FILES_TO_CONTEXT", False)

        history_limit = config.MESSAGES_LIMIT
        history_raw = await self.db.get_history(chat_id, limit=history_limit, max_db_id=max_db_id)

        contents_raw = []
        media_count = 0
        media_limit = config.MEDIA_LIMIT
        
        GOOGLE_FILE_URI_REGEX = re.compile(
            r"(https://generativelanguage\.googleapis\.com/(?:upload/)?v[0-9a-zA-Z_]+/files/[a-zA-Z0-9_-]+)",
            re.IGNORECASE
        )

        for idx, (content_obj, media_info_str) in enumerate(history_raw):
            if content_obj.parts is None:
                content_obj.parts = []

            # Substitute Google URIs in prompt strings
            new_parts = []
            for part in content_obj.parts:
                new_parts.append(part)
                if part.text:
                    uris = GOOGLE_FILE_URI_REGEX.findall(part.text)
                    for uri in uris:
                        try:
                            mime_type = await self.db.get_memory(uri)
                            if mime_type and is_gemini_supported_mime(mime_type):
                                logger.info(f"Google URI detected: {uri}. Substituting native Part.from_uri...")
                                new_parts.insert(0, types.Part.from_uri(file_uri=uri, mime_type=mime_type))
                            else:
                                logger.debug(f"Skipping Part.from_uri for URI {uri} (unsupported MIME: {mime_type})")
                        except Exception as uri_err:
                            logger.error(f"Failed to substitute Part.from_uri for {uri}: {str(uri_err)}")
            content_obj.parts = new_parts

            # Process Media Attachments (Single Files & Multi-Item Albums)
            if media_info_str:
                try:
                    media_data = json.loads(media_info_str)
                    items_list = media_data.get("items") if (isinstance(media_data, dict) and "items" in media_data) else [media_data]
                    model_virtual_parts = []

                    for item in items_list:
                        if not isinstance(item, dict):
                            continue
                        m_path = item.get("path")
                        m_type = item.get("mime_type")

                        if m_path and os.path.exists(m_path):
                            m_type = detect_mime_type(m_path, fallback_mime=m_type)
                            m_type_norm = (m_type or "").lower().strip()

                            whitelist = config.AI_ALLOWED_MIMES if config.AI_ALLOWED_MIMES and "all" not in [w.lower() for w in config.AI_ALLOWED_MIMES] else list(GEMINI_SUPPORTED_MIME_TYPES)
                            if not matches_filter(m_type_norm, whitelist, config.AI_BLOCKED_MIMES) or not is_gemini_supported_mime(m_type_norm):
                                content_obj.parts.append(
                                    types.Part.from_text(text=f"[Attached File Metadata: {os.path.basename(m_path)} ({m_type_norm}) - binary format not supported for direct vision]")
                                )
                                continue

                            # Check if files should be automatically attached as binary parts
                            if auto_attach and media_count < media_limit and file_mode != "none":
                                file_part = None
                                is_image = m_type_norm.startswith("image/")
                                file_size = os.path.getsize(m_path)
                                
                                if is_image and file_size < 4 * 1024 * 1024:
                                    with open(m_path, "rb") as f:
                                        file_bytes = f.read()
                                    file_part = types.Part.from_bytes(data=file_bytes, mime_type=m_type_norm)
                                else:
                                    file_hash = get_file_content_hash(m_path)
                                    cache_key = f"google_file_uri_{file_hash}"
                                    google_uri = await self.db.get_memory(cache_key)
                                    
                                    if not google_uri:
                                        try:
                                            upload_cfg = types.UploadFileConfig(mime_type=m_type_norm) if hasattr(types, "UploadFileConfig") else {"mime_type": m_type_norm}
                                            try:
                                                uploaded_file = await gemini_client.aio.files.upload(file=m_path, config=upload_cfg)
                                            except Exception:
                                                uploaded_file = await gemini_client.aio.files.upload(file=m_path)
                                            if await wait_for_google_file_active(gemini_client, uploaded_file.name):
                                                google_uri = uploaded_file.uri
                                                saved_m = uploaded_file.mime_type if is_gemini_supported_mime(uploaded_file.mime_type) else m_type_norm
                                                await self.db.set_memory(cache_key, google_uri)
                                                await self.db.set_memory(google_uri, saved_m)
                                        except Exception as upload_err:
                                            logger.error(f"Google upload failed for {m_path}: {str(upload_err)}")
                                            google_uri = None

                                    if google_uri:
                                        actual_mime = await self.db.get_memory(google_uri) or m_type_norm
                                        if is_gemini_supported_mime(actual_mime):
                                            file_part = types.Part.from_uri(file_uri=google_uri, mime_type=actual_mime)

                                if file_part:
                                    if content_obj.role == "user":
                                        content_obj.parts.insert(0, file_part)
                                        media_count += 1
                                    elif content_obj.role == "model":
                                        model_virtual_parts.append(file_part)
                                        media_count += 1
                            elif file_mode == "summarize":
                                summary_text = await self.generate_media_summary(gemini_client, m_path, m_type_norm)
                                content_obj.parts.append(types.Part.from_text(text=summary_text))
                            else:
                                content_obj.parts.append(
                                    types.Part.from_text(text=f"[Attached File Metadata: {os.path.basename(m_path)} ({m_type_norm}) - Call tool to inspect if needed]")
                                )
                    if model_virtual_parts:
                        contents_raw.append(content_obj)
                        virtual_content = types.Content(
                            role="user",
                            parts=[types.Part.from_text(text="[System notification: You successfully attached and displayed these media files to the chat]")] + model_virtual_parts
                        )
                        contents_raw.append(virtual_content)
                        continue
                except Exception as me_err:
                    logger.error(f"Error processing media context: {str(me_err)}")

            contents_raw.append(content_obj)

        # Apply File Trimming Strategy if FILE_CONTEXT_MODE="trim" or "hybrid"
        if file_mode in ["trim", "hybrid"]:
            trim_count = getattr(config, "FILE_TRIM_COUNT", 5)
            curr_media = 0
            for content in reversed(contents_raw):
                has_media = any(hasattr(p, "file_data") or hasattr(p, "inline_data") for p in (content.parts or []))
                if has_media:
                    curr_media += 1
                    if curr_media > trim_count:
                        content.parts = [p for p in content.parts if not (hasattr(p, "file_data") or hasattr(p, "inline_data"))]

        # Step 1: Clean out empty Part items, unsupported MIME types, and empty Content turns
        valid_contents = []
        needs_db_sanitize = False
        for c in contents_raw:
            valid_parts = []
            for p in (c.parts or []):
                # Check for unsupported MIME type in file_data
                if getattr(p, "file_data", None) is not None:
                    p_mime = getattr(p.file_data, "mime_type", None)
                    if not is_gemini_supported_mime(p_mime):
                        logger.warning(f"Sanitizing Part with unsupported file_data MIME type '{p_mime}' in turn '{c.role}'.")
                        needs_db_sanitize = True
                        valid_parts.append(types.Part.from_text(text=f"[System: File attachment with unsupported MIME '{p_mime}' omitted]"))
                        continue

                # Check for unsupported MIME type in inline_data
                if getattr(p, "inline_data", None) is not None:
                    p_mime = getattr(p.inline_data, "mime_type", None)
                    if not is_gemini_supported_mime(p_mime):
                        logger.warning(f"Sanitizing Part with unsupported inline_data MIME type '{p_mime}' in turn '{c.role}'.")
                        needs_db_sanitize = True
                        valid_parts.append(types.Part.from_text(text=f"[System: Inline media with unsupported MIME '{p_mime}' omitted]"))
                        continue

                has_fc = getattr(p, "function_call", None) is not None
                has_fr = getattr(p, "function_response", None) is not None
                has_file = getattr(p, "file_data", None) is not None
                has_inline = getattr(p, "inline_data", None) is not None
                has_text = p.text is not None and len(str(p.text).strip()) > 0

                if has_fc or has_fr or has_file or has_inline or has_text:
                    valid_parts.append(p)

            if valid_parts:
                c.parts = valid_parts
                valid_contents.append(c)

        if needs_db_sanitize:
            asyncio.create_task(self._heal_unsupported_mime("application/octet-stream", valid_contents, chat_id=str(chat_id)))

        # Step 2: Ensure strict Function Call -> Function Response alignment
        paired_contents = []
        idx = 0
        while idx < len(valid_contents):
            c = valid_contents[idx]
            has_fc = any(getattr(p, "function_call", None) is not None for p in c.parts)
            has_fr = any(getattr(p, "function_response", None) is not None for p in c.parts)

            if c.role == "model" and has_fc:
                if idx + 1 < len(valid_contents) and valid_contents[idx + 1].role == "user":
                    next_c = valid_contents[idx + 1]
                    next_has_fr = any(getattr(p, "function_response", None) is not None for p in next_c.parts)
                    if next_has_fr:
                        paired_contents.append(c)
                        paired_contents.append(next_c)
                        idx += 2
                        continue

                # Orphaned function call without following response: sanitize to text
                new_parts = []
                for p in c.parts:
                    if getattr(p, "function_call", None):
                        fc = p.function_call
                        fc_name = getattr(fc, "name", "tool")
                        new_parts.append(types.Part.from_text(text=f"[Called tool '{fc_name}']"))
                    else:
                        new_parts.append(p)
                c.parts = new_parts
                paired_contents.append(c)
                idx += 1

            elif c.role == "user" and has_fr:
                # Orphaned function response without preceding call: sanitize to text
                new_parts = []
                for p in c.parts:
                    if getattr(p, "function_response", None):
                        fr = p.function_response
                        fr_name = getattr(fr, "name", "tool")
                        new_parts.append(types.Part.from_text(text=f"[Tool '{fr_name}' result completed]"))
                    else:
                        new_parts.append(p)
                c.parts = new_parts
                paired_contents.append(c)
                idx += 1
            else:
                paired_contents.append(c)
                idx += 1

        # Step 3: Apply Text Trimming Strategy if CONTEXT_MANAGEMENT_MODE="trim"
        if text_mode == "trim":
            trim_count = getattr(config, "CONTEXT_TRIM_COUNT", 20)
            if len(paired_contents) > trim_count:
                paired_contents = paired_contents[-trim_count:]

        # Step 4: Ensure history starts with 'user' and ends with 'user'
        if paired_contents and paired_contents[0].role == "model":
            paired_contents.insert(0, types.Content(role="user", parts=[types.Part.from_text(text="[System: Context initialized]")]))

        while paired_contents and paired_contents[-1].role == "model":
            paired_contents.pop()

        if not paired_contents:
            paired_contents.append(types.Content(role="user", parts=[types.Part.from_text(text="[System: Continue context]")]))

        return paired_contents
