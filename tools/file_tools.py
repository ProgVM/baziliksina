# tools/file_tools.py
import os
import time
import json
import asyncio
import logging
from typing import List, Any
import urllib.parse
import httpx

import config
from config import WORKSPACE_DIR, DOWNLOAD_MEDIA_TIMEOUT, USER_AGENT
import tools

logger = logging.getLogger("Tools.Files")

class AIToolKitFiles:
    def save_file_to_workspace(self, filename: str, content_hex: str, **kwargs) -> str:
        """Saves text or binary data into a File inside the local bot workspace directory (bot_workspace)."""
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            data = bytes.fromhex(content_hex)
            with open(file_path, "wb") as f:
                f.write(data)
            return f"Success. File {filename} saved to local AI storage."
        except Exception as e:
            return f"Error saving file to local storage: {str(e)}"

    async def save_file_from_telegram(self, message_id: int, filename: str = None, element_indices: Any = "all", chat_id: Any = None, **kwargs) -> str:
        """
        Downloads media files from a Telegram message, album, poll, or rich message.
        Supports selecting specific element index/indices (e.g. 0, [0, 2], or "all").

        Args:
            message_id: ID of the Telegram message.
            filename: Optional target filename for saved file (if 1 item).
            element_indices: "all", int (e.g. 0), or List[int] (e.g. [0, 2]) selecting specific media items.
            chat_id: Target chat ID or username.
        """
        if not tools.client:
            return "Error: Telethon client is not initialized."

        if chat_id is None:
            try:
                chat_id = tools.current_chat_id.get()
            except LookupError:
                return "Error: Failed to determine the current chat."

        try:
            if isinstance(chat_id, str):
                try: chat_id = int(chat_id)
                except ValueError: pass

            msg = await tools.client.get_messages(chat_id, ids=message_id)
            if not msg:
                return f"Error: Message with ID {message_id} not found."

            media_candidates = []

            if getattr(msg, "grouped_id", None) is not None:
                album_msgs = await tools.client.get_messages(chat_id, limit=20, min_id=msg.id - 10, max_id=msg.id + 10)
                for a_msg in album_msgs:
                    if getattr(a_msg, "grouped_id", None) == msg.grouped_id and a_msg.media:
                        media_candidates.append((a_msg.media, f"album_item_{a_msg.id}"))

            elif msg.media and type(msg.media).__name__ == "MessageMediaPoll":
                poll_obj = msg.media.poll
                if getattr(msg.media, "attached_media", None):
                    media_candidates.append((msg.media.attached_media, "poll_attached_media"))
                if getattr(msg.media, "explanation_media", None):
                    media_candidates.append((msg.media.explanation_media, "poll_explanation_media"))
                if poll_obj and getattr(poll_obj, "media", None):
                    media_candidates.append((poll_obj.media, "poll_main_media"))
                if poll_obj and getattr(poll_obj, "answers", None):
                    for idx, opt in enumerate(poll_obj.answers):
                        if getattr(opt, "media", None):
                            media_candidates.append((opt.media, f"poll_option_{idx+1}_media"))

            elif msg.media and type(msg.media).__name__ == "MessageMediaWebPage":
                webpage = msg.media.webpage
                if type(webpage).__name__ == "WebPage":
                    if getattr(webpage, "photo", None):
                        from telethon.tl import types as tl_types
                        media_candidates.append((tl_types.MessageMediaPhoto(photo=webpage.photo), "rich_webpage_photo"))
                    if getattr(webpage, "document", None):
                        from telethon.tl import types as tl_types
                        media_candidates.append((tl_types.MessageMediaDocument(document=webpage.document), "rich_webpage_doc"))

            elif msg.media:
                media_candidates.append((msg.media, f"media_{msg.id}"))

            if not media_candidates:
                return f"Error: Message #{message_id} in chat {chat_id} does not contain any downloadable media files."

            target_indices = []
            if str(element_indices).strip().lower() in ["all", "*", "any", "none"]:
                target_indices = list(range(len(media_candidates)))
            elif isinstance(element_indices, int):
                target_indices = [element_indices]
            elif isinstance(element_indices, list):
                target_indices = [int(i) for i in element_indices if str(i).isdigit()]
            elif isinstance(element_indices, str) and "," in element_indices:
                target_indices = [int(i.strip()) for i in element_indices.split(",") if i.strip().isdigit()]

            valid_targets = [media_candidates[i] for i in target_indices if 0 <= i < len(media_candidates)]
            if not valid_targets:
                return f"Error: Specified element index {element_indices} is out of range. Available elements count: {len(media_candidates)}."

            downloaded_paths = []
            for idx, (target_media, default_label) in enumerate(valid_targets):
                if filename and len(valid_targets) == 1:
                    out_filename = filename
                else:
                    out_filename = f"{default_label}_{int(time.time())}_{idx}.bin"

                out_path = WORKSPACE_DIR / os.path.basename(out_filename)
                saved_path = await tools.client.download_media(target_media, file=str(out_path))
                if saved_path:
                    downloaded_paths.append(os.path.basename(saved_path))

            if downloaded_paths:
                return f"Success! Downloaded {len(downloaded_paths)} element(s) from message #{message_id}: {', '.join(downloaded_paths)}."
            return "Error: Failed to download media elements."
        except Exception as e:
            return f"Error downloading media from Telegram: {str(e)}"

    def read_file_from_workspace(self, filename: str, read_as_hex: bool = False, **kwargs) -> str:
        """Reads and returns the content of the specified file from the local AI working directory."""
        file_path = WORKSPACE_DIR / os.path.basename(filename)
        if not file_path.exists() or not file_path.is_file():
            return f"Error: File '{filename}' not found."
        
        resolved_path = os.path.abspath(file_path)
        filename_only = os.path.basename(resolved_path)
        from utils import matches_filter
        if not matches_filter(filename_only, config.SANDBOX_ALLOWED_FILES, config.SANDBOX_BLOCKED_FILES):
            return "Security error: Access to this file is blocked by sandbox policy."

        try:
            if read_as_hex:
                with open(file_path, "rb") as f:
                    return f.read().hex()
            else:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    return content[:5000] + "\n[Output truncated, file too long]" if len(content) > 5000 else content
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def list_workspace_files(self, **kwargs) -> List[str]:
        """Returns a list of names of all files stored in the local AI working directory."""
        try:
            return os.listdir(WORKSPACE_DIR)
        except Exception as e:
            return [f"Error reading directory: {str(e)}"]

    def delete_file_from_workspace(self, filename: str, **kwargs) -> str:
        """Deletes the specified file from the local AI working directory."""
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            if file_path.exists():
                file_path.unlink()
                return f"Success. File {filename} deleted."
            return f"Error: File {filename} not found."
        except Exception as e:
            return f"Error deleting file: {str(e)}"

    async def forward_messages(self, message_ids: List[int], from_chat_id: Any = None, to_chat_id: Any = None, custom_messages: List[str] = None, order: str = "after", **kwargs) -> str:
        """Forwards one or multiple messages cleanly from a source chat to a target chat."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if from_chat_id is None:
            try: from_chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine source chat."
        if to_chat_id is None:
            try: to_chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(from_chat_id, str):
            try: from_chat_id = int(from_chat_id)
            except ValueError: pass
        if isinstance(to_chat_id, str):
            try: to_chat_id = int(to_chat_id)
            except ValueError: pass
        try:
            if custom_messages:
                new_custom = []
                for msg in custom_messages:
                    if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                        msg = await tools.ai_manager.executor.parse_execute_and_strip_tags(msg, to_chat_id, None, str(to_chat_id))
                    new_custom.append(msg)
                custom_messages = new_custom

            res_messages = []
            if custom_messages and order == "before":
                for msg in custom_messages:
                    res = await tools.client.send_message(to_chat_id, msg)
                    res_messages.append(res.id)
            forwards = await tools.client.forward_messages(to_chat_id, message_ids, from_chat_id, **kwargs)
            if isinstance(forwards, list):
                for f in forwards: res_messages.append(f.id)
            else:
                res_messages.append(forwards.id)
            if custom_messages and order == "after":
                for msg in custom_messages:
                    res = await tools.client.send_message(to_chat_id, msg)
                    res_messages.append(res.id)
            if tools.db:
                f_info_str = f"[Forwarded {len(message_ids)} messages from {from_chat_id} to {to_chat_id}]"
                await tools.db.save_message(str(to_chat_id), "model", f_info_str, msg_id=res_messages[-1] if res_messages else None)
                for m_id in res_messages:
                    tools.processed_msg_ids.add((int(to_chat_id), m_id))
            return f"Success. Forwarded {len(message_ids)} messages to chat {to_chat_id}."
        except Exception as e:
            return f"Error forwarding messages: {str(e)}"

    async def send_uncompressed_file(self, filename: str, chat_id: Any = None, caption: str = None, **kwargs) -> str:
        """Sends any local file strictly as an uncompressed document to preserve full original quality."""
        if not tools.client:
            return "Error: Telethon client is not initialized."
        if chat_id is None:
            try: chat_id = tools.current_chat_id.get()
            except LookupError: return "Error: Failed to determine target chat."
        if isinstance(chat_id, str):
            try: chat_id = int(chat_id)
            except ValueError: pass

        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            if not file_path.exists():
                return f"Error: File '{filename}' not found."

            if caption and tools.ai_manager and hasattr(tools.ai_manager, "executor"):
                caption = await tools.ai_manager.executor.parse_execute_and_strip_tags(caption, chat_id, None, str(chat_id))

            edit_message_id = kwargs.pop("edit_message_id", None)
            if edit_message_id:
                result = await tools.client.edit_message(chat_id, int(edit_message_id), file=str(file_path.resolve()), text=caption, force_document=True, **kwargs)
                return f"Success. Message #{edit_message_id} edited with uncompressed document."

            result = await tools.client.send_file(chat_id, str(file_path.resolve()), caption=caption, force_document=True, **kwargs)
            if tools.db:
                await tools.db.save_message(str(chat_id), "model", caption or f"[Sent Document: {filename}]", msg_id=result.id)
                tools.processed_msg_ids.add((int(chat_id), result.id))
            return f"Success. Uncompressed document sent. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending document: {str(e)}"

    async def download_content_from_url(self, url: str, filename: str = None, timeout: float = DOWNLOAD_MEDIA_TIMEOUT, **kwargs) -> str:
        """Downloads any media content, video clips, or documents from the specified link (URL)."""
        is_streaming = any(domain in url.lower() for domain in ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "twitter.com", "x.com", "vimeo.com", "soundcloud.com", "reddit.com"])
        out_filename = filename if filename else "downloaded_media"
        out_path = WORKSPACE_DIR / os.path.basename(out_filename)
        
        if is_streaming:
            try:
                logger.info(f"Streaming service detected. Launching yt_dlp to download {url}...")
                import yt_dlp
                ydl_opts = {
                    'outtmpl': str(WORKSPACE_DIR / '%(title)s.%(ext)s'),
                    'format': 'bestvideo+bestaudio/best',
                    'merge_output_format': 'mp4',
                    'quiet': True,
                    'noprogress': True
                }
                if kwargs:
                    ydl_opts.update(kwargs)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                    actual_filename = ydl.prepare_filename(info)
                    if filename:
                        from pathlib import Path
                        actual_path = Path(actual_filename)
                        if actual_path.exists():
                            ext = actual_path.suffix
                            out_path = out_path.with_suffix(ext)
                            actual_path.rename(out_path)
                            actual_filename = str(out_path.resolve())
                    logger.info(f"File successfully downloaded via yt_dlp: {actual_filename}")
                    return f"Success. Streaming media content downloaded and saved to the working folder as '{os.path.basename(actual_filename)}'."
            except Exception as e:
                logger.error(f"Download failed via yt_dlp: {str(e)}. Trying direct download...")

        headers = {"User-Agent": USER_AGENT}
        try:
            logger.info(f"Launching direct file download from link {url}...")
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code == 200:
                    content_bytes = resp.content
                    if not filename:
                        parsed_url = urllib.parse.urlparse(url)
                        url_filename = os.path.basename(parsed_url.path)
                        if url_filename and "." in url_filename:
                            out_filename = url_filename
                        else:
                            content_type = resp.headers.get("Content-Type", "")
                            ext = ".bin"
                            if "image/jpeg" in content_type: ext = ".jpg"
                            elif "image/png" in content_type: ext = ".png"
                            elif "application/pdf" in content_type: ext = ".pdf"
                            elif "audio/mpeg" in content_type: ext = ".mp3"
                            elif "video/mp4" in content_type: ext = ".mp4"
                            out_filename = f"downloaded_file_{int(time.time())}{ext}"
                    out_path = WORKSPACE_DIR / os.path.basename(out_filename)
                    with open(out_path, "wb") as f:
                        f.write(content_bytes)
                    logger.info(f"File successfully downloaded directly and saved: {out_path}")
                    return f"Success. Static file downloaded and saved under '{os.path.basename(out_filename)}'."
                else:
                    return f"Direct download error. Server returned status code {resp.status_code}."
        except Exception as e:
            return f"Critical error during direct file download: {str(e)}"

# Export methods to module level
toolkit_files = AIToolKitFiles()
for attr in dir(toolkit_files):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_files, attr)
