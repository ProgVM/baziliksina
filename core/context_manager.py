# core/context_manager.py
import os
import config
import json
import logging
import re
import hashlib
import asyncio
from pathlib import Path
from google.genai import types
from google.genai.errors import APIError

from utils import wait_for_google_file_active

logger = logging.getLogger("ContextManager")



class AIContextManager:
    """
    Manages complete dialogue history retrieval, token limits calculation,
    multimodal asset binding, and externalized prompt-based summarization.
    """
    def __init__(self, db_manager, key_manager):
        self.db = db_manager
        self.key_manager = key_manager

    async def summarize_chat_context(self, gemini_client):
        """
        Compresses the global cross-cutting history log using an externalized prompt.
        """
        logger.info("Context limit exceeded. Starting global summarization of cross-cutting memory...")
        history_raw = await self.db.get_history("global", limit=config.SUMMARIZATION_MESSAGES_LIMIT)
        
        # Read the externalized summarization prompt
        prompt_path = config.BASE_DIR / "config" / "summarize_prompt.txt"
        if prompt_path.exists():
            try:
                with open(prompt_path, "r", encoding="utf-8") as f:
                    prompt = f.read().strip()
            except Exception as e:
                logger.error(f"Error reading summarize_prompt.txt: {str(e)}")
                prompt = "Provide a brief summary of the following global chat history."
        else:
            prompt = "Provide a brief summary of the following global chat history."

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
            await self.db.update_summary("global", summary_text)
            await self.db.clear_history_for_summarization("global", keep_last_n=config.SUMMARIZATION_KEEP_LIMIT)
            logger.info("Global summarization of cross-cutting memory completed successfully.")
        except Exception as e:
            logger.error(f"Error during summarization: {str(e)}")

    async def _heal_inaccessible_file(self, file_id: str, contents: list):
        """
        Permanently sanitizes the local SQLite database and active session context 
        to remove an inaccessible Google File URI after key rotations.
        """
        logger.info(f"Inaccessible File ID identified: {file_id}. Sanitizing database context...")
        try:
            import re
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

            # Wipe matching cache keys from shared_memory to trigger re-upload on next turns
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
            logger.info(f"Permanently sanitized database row(s) containing File ID {file_id} from both text and raw_content_json fields.")
        except Exception as db_clean_err:
            logger.error(f"Failed to sanitize database for File ID {file_id}: {str(db_clean_err)}")
        
        # Clean active in-memory contents to retry immediately
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
        Loads the history from SQLite, performs dynamic on-the-fly Google Files uploads,
        matches cached file URIs, and returns chronological contents ready for Gemini.
        """
        # Load the configuration of cross-cutting memory dynamically
        if config.CROSS_CHAT_CONTEXT:
            history_raw = await self.db.get_history(chat_id, limit=config.MESSAGES_LIMIT, max_db_id=max_db_id)
        else:
            # Fallback to isolated context of current chat only (no other chats logged)
            history_raw = await self.db.get_history(chat_id, limit=config.MESSAGES_LIMIT, max_db_id=max_db_id)
            # Remove segments corresponding to other chats if present in global scope
            history_raw = [(c, m) for (c, m) in history_raw if f"Chat: {chat_id}" in str(c.parts or "")]

        contents_raw = []
        media_limit = config.MEDIA_LIMIT
        media_count = 0
        
        GOOGLE_FILE_URI_REGEX = re.compile(
            r"(https://generativelanguage\.googleapis\.com/(?:upload/)?v[0-9a-zA-Z_]+/files/[a-zA-Z0-9_-]+)",
            re.IGNORECASE
        )
        
        for idx, (content_obj, media_info_str) in enumerate(history_raw):
            if content_obj.parts is None:
                content_obj.parts = []
            
            # Map Google URIs inside prompt strings dynamically to native Part.from_uri
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

            is_within_limit = media_count < media_limit
            if media_info_str and is_within_limit:
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
                        if "webm" in m_type_norm or m_path.endswith(".webm"):
                            continue
                            
                        # Evaluate allowed and blocked MIME-types dynamically via config filters
                        from utils import matches_filter
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
                            logger.info(f"Skipping file {m_path} due to MIME type filter constraints: {m_type}")
                            continue

                        from downloader import check_and_clean_corrupted_file
                        if not check_and_clean_corrupted_file(m_path, m_type):
                            await self.db.db.execute(
                                "UPDATE messages SET media_info = NULL WHERE media_info LIKE ?", 
                                (f"%{os.path.basename(m_path)}%",)
                            )
                            await self.db.db.commit()
                        else:
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
                                        logger.info(f"Uploading file '{m_path}' to Google Files API...")
                                        uploaded_file = await gemini_client.aio.files.upload(file=m_path)
                                        google_uri = uploaded_file.uri
                                        
                                        from utils import wait_for_google_file_active
                                        if await wait_for_google_file_active(gemini_client, uploaded_file.name):
                                            await self.db.set_memory(cache_key, google_uri)
                                            await self.db.set_memory(google_uri, uploaded_file.mime_type)
                                        else:
                                            google_uri = None
                                    except Exception as upload_err:
                                        logger.error(f"Google upload failed for {m_path}: {str(upload_err)}")
                                        google_uri = None
                                if google_uri:
                                    file_part = types.Part.from_uri(file_uri=google_uri, mime_type=m_type)

                            if file_part:
                                if content_obj.role == "user":
                                    content_obj.parts.insert(0, file_part)
                                    media_count += 1
                                elif content_obj.role == "model":
                                    # Bypass modelTurn media restriction with virtual userTurn representation!
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
                except Exception as me_err:
                    logger.error(f"Error loading media data: {str(me_err)}")

            contents_raw.append(content_obj)
        # Chronologically align raw contents
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
                
        return aligned
