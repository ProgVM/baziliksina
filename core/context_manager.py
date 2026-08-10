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
from utils import wait_for_google_file_active, matches_filter

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
            text_parts = [p.text for p in (content_obj.parts or []) if p.text]
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
        file_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
        summary_key = f"media_summary_{file_hash}"
        
        cached_summary = await self.db.get_memory(summary_key)
        if cached_summary:
            return cached_summary

        logger.info(f"Generating AI visual/audio summary for media file: {file_path}")
        try:
            file_size = os.path.getsize(file_path)
            if file_size < 4 * 1024 * 1024 and mime_type.startswith("image/"):
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            else:
                uploaded_file = await gemini_client.aio.files.upload(file=file_path)
                if not await wait_for_google_file_active(gemini_client, uploaded_file.name):
                    return "[Media summary unavailable: file processing timeout]"
                file_part = types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type)

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
            return f"[Media Attachment: {os.path.basename(file_path)} ({mime_type})]"

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
                                    if p.get("file_data") and "[File inaccessible" in str(p.get("file_data")):
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
        evaluates explicit file attachment rules, aligns turn roles, and prepares Gemini API contents payload.
        """
        # Read strategy configurations dynamically from config
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
                            if mime_type:
                                logger.info(f"Google URI detected: {uri}. Substituting native Part.from_uri...")
                                new_parts.insert(0, types.Part.from_uri(file_uri=uri, mime_type=mime_type))
                        except Exception as uri_err:
                            logger.error(f"Failed to substitute Part.from_uri for {uri}: {str(uri_err)}")
            content_obj.parts = new_parts

            # Process Media Attachments according to FILE_CONTEXT_MODE and AUTO_ATTACH_FILES_TO_CONTEXT
            if media_info_str:
                try:
                    media_data = json.loads(media_info_str)
                    m_path = media_data.get("path")
                    m_type = media_data.get("mime_type")
                    
                    if m_type == "media" and m_path:
                        import mimetypes
                        guessed, _ = mimetypes.guess_type(m_path)
                        m_type = guessed or "application/octet-stream"

                    if m_path and os.path.exists(m_path) and m_type:
                        m_type_norm = m_type.lower().strip()
                        
                        # Evaluate MIME filters
                        gemini_supported = [
                            "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
                            "video/mp4", "video/mpeg", "video/quicktime", "video/x-msvideo", 
                            "video/x-flv", "video/webm", "video/x-ms-wmv", "video/3gpp",
                            "audio/wav", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/aac", 
                            "audio/flac", "audio/x-m4a", "audio/mp4", "audio/amr",
                            "text/plain", "text/html", "text/css", "text/javascript", 
                            "text/rtf", "text/xml", "text/markdown", "application/pdf", 
                            "application/json", "text/csv", "text/tsv"
                        ]
                        whitelist = config.AI_ALLOWED_MIMES if config.AI_ALLOWED_MIMES and "all" not in [w.lower() for w in config.AI_ALLOWED_MIMES] else gemini_supported
                        if not matches_filter(m_type_norm, whitelist, config.AI_BLOCKED_MIMES):
                            continue

                        # Check if files should be automatically attached as binary parts
                        if auto_attach and media_count < media_limit and file_mode != "none":
                            file_part = None
                            is_image = m_type.startswith("image/")
                            file_size = os.path.getsize(m_path)
                            
                            if is_image and file_size < 4 * 1024 * 1024:
                                with open(m_path, "rb") as f:
                                    file_bytes = f.read()
                                file_part = types.Part.from_bytes(data=file_bytes, mime_type=m_type)
                            else:
                                file_hash = hashlib.md5(m_path.encode('utf-8')).hexdigest()
                                cache_key = f"google_file_uri_{file_hash}"
                                google_uri = await self.db.get_memory(cache_key)
                                
                                if not google_uri:
                                    try:
                                        uploaded_file = await gemini_client.aio.files.upload(file=m_path)
                                        if await wait_for_google_file_active(gemini_client, uploaded_file.name):
                                            google_uri = uploaded_file.uri
                                            await self.db.set_memory(cache_key, google_uri)
                                            await self.db.set_memory(google_uri, uploaded_file.mime_type)
                                    except Exception as upload_err:
                                        logger.error(f"Google upload failed for {m_path}: {str(upload_err)}")
                                        google_uri = None

                                if google_uri:
                                    actual_mime = await self.db.get_memory(google_uri) or m_type
                                    file_part = types.Part.from_uri(file_uri=google_uri, mime_type=actual_mime)

                            if file_part:
                                if content_obj.role == "user":
                                    content_obj.parts.insert(0, file_part)
                                    media_count += 1
                                elif content_obj.role == "model":
                                    virtual_content = types.Content(
                                        role="user",
                                        parts=[
                                            types.Part.from_text(text="[System notification: You successfully attached and displayed this media file to the chat]"),
                                            file_part
                                        ]
                                    )
                                    contents_raw.append(content_obj)
                                    contents_raw.append(virtual_content)
                                    media_count += 1
                                    continue
                        elif file_mode == "summarize":
                            # Generate/Retrieve lightweight text summary instead of heavy binary bytes
                            summary_text = await self.generate_media_summary(gemini_client, m_path, m_type)
                            content_obj.parts.append(types.Part.from_text(text=summary_text))
                        else:
                            # File logged as metadata reference only (AUTO_ATTACH_FILES_TO_CONTEXT=False)
                            content_obj.parts.append(
                                types.Part.from_text(text=f"[Attached File Metadata: {os.path.basename(m_path)} ({m_type}) - Call tool to inspect if needed]")
                            )
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

        # Chronologically align raw contents and manage role turns
        aligned = []
        skip_indices = set()
        
        for i, content in enumerate(contents_raw):
            if i in skip_indices:
                continue
                
            has_fc = any(part.function_call for part in (content.parts or []))
            if content.role == "model" and has_fc:
                aligned.append(content)
                for j in range(i + 1, len(contents_raw)):
                    if j in skip_indices:
                        continue
                    sub_content = contents_raw[j]
                    has_fr = any(part.function_response for part in (sub_content.parts or []))
                    if sub_content.role == "user" and has_fr:
                        aligned.append(sub_content)
                        skip_indices.add(j)
                        break
            elif content.role == "user" and any(part.function_response for part in (content.parts or [])):
                continue
            else:
                aligned.append(content)

        # Apply Text Trimming Strategy if CONTEXT_MANAGEMENT_MODE="trim"
        if text_mode == "trim":
            trim_count = getattr(config, "CONTEXT_TRIM_COUNT", 20)
            if len(aligned) > trim_count:
                aligned = aligned[-trim_count:]

        # Guard against Gemini API 400 error: 'Requests ending with a model turn are not supported'
        while aligned and aligned[-1].role == "model":
            aligned.pop()

        if not aligned:
            aligned.append(types.Content(role="user", parts=[types.Part.from_text(text="[System: Continue context]")]))

        return aligned
