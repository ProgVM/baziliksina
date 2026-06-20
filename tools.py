# tools.py
import os
import json
import asyncio
import logging
import subprocess
import re
import inspect
import time
from typing import List, Dict, Any
from contextvars import ContextVar
import urllib.parse
from bs4 import BeautifulSoup
import httpx
from google.genai import types

from config import (
    WORKSPACE_DIR, TELEGRAM_METHOD_BLACKLIST, TOR_SOCKS_PORT, TOR_CONTROL_PORT,
    TOR_ROTATION_TIMEOUT, POLLINATIONS_MAX_ATTEMPTS, TOR_MAX_CONSECUTIVE_FAILURES,
    SQL_SELECT_LIMIT, SQL_STDOUT_CHAR_LIMIT, WEB_SEARCH_RESULTS_LIMIT,
    SCRAPE_CHAR_LIMIT, BOT_RESPONSE_TIMEOUT, DEFAULT_RESULT_INDEX, BUTTON_CLICK_TIMEOUT, DOWNLOAD_MEDIA_TIMEOUT,
    USER_AGENT, TOR_HOST, TOR_PASSWORD, WEB_SEARCH_TIMEOUT, WEB_MEDIA_SEARCH_TIMEOUT, SCRAPE_TIMEOUT,
    DEFAULT_IMAGE_MODEL, DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, GENERATE_IMAGE_TIMEOUT,
    DEFAULT_AUDIO_VOICE, DEFAULT_AUDIO_MODEL, GENERATE_AUDIO_TIMEOUT,
    DEFAULT_VIDEO_MODEL, DEFAULT_VIDEO_DURATION, DEFAULT_VIDEO_ASPECT_RATIO, GENERATE_VIDEO_TIMEOUT,
    GOOGLE_UPLOAD_TIMEOUT, DEFAULT_PUBLIC_UPLOAD_PROVIDER, PUBLIC_UPLOAD_TIMEOUT
)

logger = logging.getLogger("Tools")

# Global context variables and bot core references
current_chat_id: ContextVar[int] = ContextVar("current_chat_id")
current_reply_to_id: ContextVar[int] = ContextVar("current_reply_to_id")
client = None
db = None
key_manager = None
pollinations_key_manager = None
bot_callback_fn = None
ai_manager = None

# Regular expressions to protect the virtual machine sandbox
FORBIDDEN_SHELL_REGEX = re.compile(
    r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|tools\.py|sandbox\.py|utils\.py|downloader\.py)", 
    re.IGNORECASE
)


async def rotate_tor_ip() -> bool:
    """
    Asynchronously connects to the local Tor control port
    and sends a NEWNYM signal for instant forced rotation of the outbound IP address.
    """
    try:
        coro = asyncio.open_connection(TOR_HOST, TOR_CONTROL_PORT)
        reader, writer = await asyncio.wait_for(coro, timeout=TOR_ROTATION_TIMEOUT)
        
        writer.write(f'AUTHENTICATE "{TOR_PASSWORD}"\r\n'.encode('utf-8'))
        await writer.drain()
        resp1 = await reader.readline()
        
        if b"250" in resp1:
            writer.write(b'SIGNAL NEWNYM\r\n')
            await writer.drain()
            resp2 = await reader.readline()
            
            if b"250" in resp2:
                logger.info("Tor successfully processed the NEWNYM command. Outbound IP address changed!")
                writer.close()
                await writer.wait_closed()
                return True
                
        writer.close()
        await writer.wait_closed()
    except Exception as e:
        logger.warning(f"Failed to send Tor rotation signal (check if Tor is running with the --ControlPort key {TOR_CONTROL_PORT}): {str(e)}")
    return False


async def call_pollinations_api(url: str, params: dict, timeout: float) -> httpx.Response:
    f"""
    Universal asynchronous method for executing requests to the Pollinations API.
    - For Secret keys (sk_): on failures, immediately rotates keys without changing IP in Tor.
    - For App keys (pk_): rotates IP in Tor an infinite number of times as long as it brings success.
    Rotates the App key itself to the next one only if {{POLLINATIONS_MAX_ATTEMPTS}} consecutive IP rotation attempts fail to resolve the 429 issue.
    """
    global pollinations_key_manager
    
    # Resolve Tor proxy URL programmatically to keep Gemini direct and fast
    # Double protection: only route through Tor if it is verified to be active
    from proxy_manager import proxy_rotator
    proxy_url = proxy_rotator.get_proxy("pollinations")
    
    num_keys = len(pollinations_key_manager.keys) if (pollinations_key_manager and pollinations_key_manager.keys) else 1
    max_attempts = max(POLLINATIONS_MAX_ATTEMPTS, num_keys * 4)
    
    consecutive_ip_failures = 0
    tor_rotated_last_turn = False
    
    for attempt in range(max_attempts):
        current_key = await pollinations_key_manager.get_active_key() if pollinations_key_manager else ""
        
        req_params = params.copy()
        if current_key:
            req_params["key"] = current_key
        else:
            req_params.pop("key", None)
            
        try:
            logger.info(f"Request to Pollinations (Attempt {attempt+1}/{max_attempts}, Key: {current_key[:10]}...)...")
            
            # Execute request via Tor proxy, with automatic fallback to direct connection
            try:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client_httpx:
                    resp = await client_httpx.get(url, params=req_params)
            except Exception as proxy_err:
                logger.warning(f"Tor SOCKS5 proxy at {proxy_url} is unavailable. Falling back to direct connection...")
                async with httpx.AsyncClient(timeout=timeout) as client_httpx:
                    resp = await client_httpx.get(url, params=req_params)
                
                # If we received a rate limit (429) or authorization (401/402) error
                if resp.status_code in [401, 402, 429]:
                    logger.warning(f"Pollinations returned error {resp.status_code} for key {current_key[:10]}...")
                    
                    is_pk_key = current_key.startswith("pk_") or current_key.startswith("plln_pk_")
                    
                    if is_pk_key:
                        if tor_rotated_last_turn:
                            consecutive_ip_failures += 1
                            logger.warning(f"IP rotation did not resolve the 429 issue in the previous step (consecutive failures: {consecutive_ip_failures}/{TOR_MAX_CONSECUTIVE_FAILURES}).")
                        else:
                            consecutive_ip_failures = 0
                            
                        if consecutive_ip_failures < TOR_MAX_CONSECUTIVE_FAILURES:
                            tor_rotated = await rotate_tor_ip()
                            if tor_rotated:
                                tor_rotated_last_turn = True
                                continue
                            else:
                                logger.warning("Tor is unavailable for IP rotation.")
                                
                    logger.warning("IP rotation did not help. Switching to the next key.")
                    if pollinations_key_manager:
                        await pollinations_key_manager.rotate_key_async()
                    consecutive_ip_failures = 0
                    tor_rotated_last_turn = False
                    continue
                    
                tor_rotated_last_turn = False
                return resp
                
        except Exception as e:
            logger.error(f"Pollinations request execution failed: {str(e)}")
            is_pk_key = current_key.startswith("pk_") or current_key.startswith("plln_pk_")
            
            if is_pk_key:
                if tor_rotated_last_turn:
                    consecutive_ip_failures += 1
                else:
                    consecutive_ip_failures = 0
                    
                if consecutive_ip_failures < TOR_MAX_CONSECUTIVE_FAILURES:
                    tor_rotated = await rotate_tor_ip()
                    if tor_rotated:
                        tor_rotated_last_turn = True
                        await asyncio.sleep(1.0)
                        continue
            
            if pollinations_key_manager and len(pollinations_key_manager.keys) > 1:
                await pollinations_key_manager.rotate_key_async()
            consecutive_ip_failures = 0
            tor_rotated_last_turn = False
            await asyncio.sleep(1.0)


class AIToolKit:
    """Universal OOP toolkit class available to the AI for managing the Telegram account and system."""

    # =====================================================================
    # CATEGORY 1: File System and Sandbox (Workspace File Management)
    # =====================================================================

    def save_file_to_workspace(self, filename: str, content_hex: str, **kwargs) -> str:
        """
        Saves text or binary data into a File inside the local bot workspace directory (bot_workspace).
        Used when you need to create a new local File on disk or write updated information.

        Args:
            filename: The name of the file to be created or overwritten (e.g., 'notes.txt', 'config.json' or 'image.jpg').
            content_hex: The file content passed in hexadecimal (hex) format. Before writing, the string is automatically decoded into binary.
        """
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            data = bytes.fromhex(content_hex)
            with open(file_path, "wb") as f:
                f.write(data)
            return f"Success. File {filename} saved to local AI storage."
        except Exception as e:
            return f"Error saving file to local storage: {str(e)}"

    async def save_file_from_telegram(self, message_id: int, filename: str, chat_id: Any = None, **kwargs) -> str:
        """
        Downloads a media file or document from the specified Telegram message in the chosen chat
        and saves it into the local sandbox folder (bot_workspace) under a selected name.
        Used when you need to download a File sent in the current or any other chat/channel.

        Args:
            message_id: The ID of the message in the chat containing the media file to download.
            filename: The name under which the File will be saved in the sandbox (e.g., 'user_photo.jpg' or 'report.pdf').
            chat_id: Optional ID or username of the chat/channel from which the File is downloaded. If not specified, the current chat is used.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        
        if chat_id is None:
            try:
                chat_id = current_chat_id.get()
            except LookupError:
                return "Error: Failed to determine the current chat."

        try:
            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass

            msg = await client.get_messages(chat_id, ids=message_id)
            if not msg or not msg.media:
                return f"Error: Message with ID {message_id} not found in chat {chat_id} or does not contain media files."
            
            out_path = WORKSPACE_DIR / os.path.basename(filename)
            path = await client.download_media(msg, file=str(out_path))
            if path:
                return f"Success. File from message #{message_id} of chat {chat_id} saved under the name '{filename}'."
            return "Error: Failed to download file."
        except Exception as e:
            return f"Error downloading file from Telegram: {str(e)}"

    def read_file_from_workspace(self, filename: str, read_as_hex: bool = False, **kwargs) -> str:
        """
        Reads and returns the content of the specified file from the local AI working directory (bot_workspace).
        Allows the AI to read text notes, scripts, JSON configs, or get a hexadecimal dump of a binary file.

        Args:
            filename: The name of the file in the sandbox to read (e.g., 'notes.txt').
            read_as_hex: If True, the file is read as binary and returned in hexadecimal (hex) format. Default is False.
        """
        file_path = WORKSPACE_DIR / os.path.basename(filename)
        if not file_path.exists() or not file_path.is_file():
            return f"Error: File '{filename}' not found."
        
        resolved_path = os.path.abspath(file_path)
        if any(x in resolved_path for x in ["bot.py", "config.py", "db_manager.py", "key_manager.py", "gemini_manager.py", ".env", "tools.py", "sandbox.py", "utils.py", "downloader.py", "registry.py"]):
            return "Security error: Access to bot system files is blocked."

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
        """
        Returns a list of names of all files stored in the local AI working directory (bot_workspace).
        Used to check which files are available in the storage for reading, sending, or deleting.
        """
        try:
            return os.listdir(WORKSPACE_DIR)
        except Exception as e:
            return [f"Error reading directory: {str(e)}"]

    def delete_file_from_workspace(self, filename: str, **kwargs) -> str:
        """
        Deletes the specified file from the local AI working directory (bot_workspace).
        Used to clean up unnecessary temporary files, old images, or logs.
        
        Args:
            filename: The name of the file to be deleted (e.g., 'temp.jpg').
        """
        try:
            file_path = WORKSPACE_DIR / os.path.basename(filename)
            if file_path.exists():
                file_path.unlink()
                return f"Success. File {filename} deleted from local storage."
            return f"Error: File {filename} not found."
        except Exception as e:
            return f"Error deleting file: {str(e)}"

    async def download_content_from_url(self, url: str, filename: str = None, timeout: float = DOWNLOAD_MEDIA_TIMEOUT, **kwargs) -> str:
        f"""
        Downloads any media content, video clips, audio files, or documents from the specified link (URL)
        into the local AI storage (bot_workspace).
        The tool automatically detects the source of the link:
        - Streaming platforms (YouTube, TikTok, Instagram, Twitter/X, Reddit, SoundCloud, Vimeo): utilizes the advanced
          'yt_dlp' library for automatic seamless extraction of media with sound in the best available quality (mp4/mp3).
        - Direct links to static files (images, PDF documents, archives, audio files): downloads the file directly via httpx.

        RULE FOR AI: After you have successfully downloaded a file using this tool, you are CATEGORICALLY UNABLE
        to see or analyze its content upon download! To view or listen to this downloaded file,
        you MUST immediately call the 'upload_file_to_google' tool (passing the name of this downloaded file) to send it
        to Gemini servers and natively 'see'/'hear' its content on the next generation step!

        Args:
            url: Full web link to download the media file or document (e.g., 'https://www.youtube.com/watch?v=...' or 'https://example.com/file.pdf').
            filename: Optional name under which the file will be saved in the sandbox (e.g., 'custom_video.mp4'). If not specified, the filename will be determined automatically.
            timeout: Operation execution timeout in seconds. Default is {DOWNLOAD_MEDIA_TIMEOUT}.
        """
        import urllib.parse
        from pathlib import Path
        
        # 1. Check if the link is streaming (requires yt_dlp)
        is_streaming = any(domain in url.lower() for domain in ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "twitter.com", "x.com", "vimeo.com", "soundcloud.com", "reddit.com"])
        
        out_filename = filename if filename else "downloaded_media"
        out_path = WORKSPACE_DIR / os.path.basename(out_filename)
        
        if is_streaming:
            try:
                logger.info(f"Streaming service detected. Launching yt_dlp to download {url}...")
                import yt_dlp
                
                # yt_dlp settings for cross-platform downloading in best quality
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
                    # Execute in a separate thread to avoid blocking asyncio
                    info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                    actual_filename = ydl.prepare_filename(info)
                    
                    # If the AI explicitly passed the output filename, rename it
                    if filename:
                        actual_path = Path(actual_filename)
                        if actual_path.exists():
                            ext = actual_path.suffix
                            out_path = out_path.with_suffix(ext)
                            actual_path.rename(out_path)
                            actual_filename = str(out_path.resolve())
                            
                    logger.info(f"File successfully downloaded via yt_dlp: {actual_filename}")
                    return (
                        f"Success. Streaming media content downloaded and saved to the working folder as '{os.path.basename(actual_filename)}'.\n"
                        f"[WARNING]: If you want to view or listen to this downloaded file, "
                        f"you MUST immediately call the 'upload_file_to_google' tool (specifying the name of this file) to see/hear its content!"
                    )
            except Exception as e:
                logger.error(f"Download failed via yt_dlp: {str(e)}. Trying direct download...")

        # 2. Direct download of static file via httpx
        headers = {
            "User-Agent": USER_AGENT
        }
        try:
            logger.info(f"Launching direct file download from link {url}...")
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code == 200:
                    content_bytes = resp.content
                    
                    # If the filename is omitted, determine it by URL or by Content-Type header
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
                    return (
                        f"Success. Static file downloaded and saved to the working folder under the name '{os.path.basename(out_filename)}'.\n"
                        f"[WARNING]: If you want to view or listen to this downloaded file, "
                        f"you MUST immediately call the 'upload_file_to_google' tool (specifying the name of this file) to see/hear its content!"
                    )
                else:
                    return f"Direct download error. Server returned status code {resp.status_code}."
        except Exception as e:
            return f"Critical error during direct file download: {str(e)}"

    # =====================================================================
    # CATEGORY 2: Web Search & Data Scraping (Web Search & Scraping)
    # =====================================================================

    async def internet_search(self, query: str, timeout: float = WEB_SEARCH_TIMEOUT, **kwargs) -> str:
        f"""
        Performs a text search on the Internet for a given query via DuckDuckGo and returns brief results.
        Used when the user needs up-to-date information from the outside world, news, or reference data.
        
        Args:
            query: Text search query (e.g., 'current dollar rate' or 'latest Telegram news').
            timeout: Server response timeout in seconds. Default is {WEB_SEARCH_TIMEOUT}.
        """
        headers = {
            "User-Agent": USER_AGENT
        }
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code != 200:
                    return f"Search failed, error code: {resp.status_code}"
                
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for link in soup.find_all("a", class_="result__snippet")[:WEB_SEARCH_RESULTS_LIMIT]:
                    results.append(link.get_text(strip=True))
                return "\n\n".join(results) if results else "Search returned no results."
        except Exception as e:
            return f"Search error: {str(e)}"

    async def internet_media_search(self, query: str, media_type: str = "image", timeout: float = WEB_MEDIA_SEARCH_TIMEOUT, **kwargs) -> str:
        f"""
        Performs a search for multimedia files or PDF documents on the Internet via DuckDuckGo.

        Args:
            query: Search query.
            media_type: Category of media files to search for ('image', 'video', or 'document').
            timeout: Server response timeout in seconds. Default is {WEB_MEDIA_SEARCH_TIMEOUT}.
        """
        headers = {
            "User-Agent": USER_AGENT
        }
        search_query = query
        if media_type == "document":
            search_query += " filetype:pdf"
        elif media_type == "image":
            search_query += " format:jpg"
            
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code != 200:
                    return f"Media search failed, code: {resp.status_code}"
                
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                if media_type in ["image", "document"]:
                    for link in soup.find_all("a", class_="result__url")[:WEB_SEARCH_RESULTS_LIMIT]:
                        href = link.get("href", "")
                        if "uddg=" in href:
                            actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                            results.append(actual_url)
                else:
                    for link in soup.find_all("a", class_="result__snippet")[:WEB_SEARCH_RESULTS_LIMIT]:
                        results.append(link.get_text(strip=True))
                return "\n".join(results) if results else "Multimedia not found."
        except Exception as e:
            return f"Error searching for media: {str(e)}"

    async def scrape_url(self, url: str, timeout: float = SCRAPE_TIMEOUT, **kwargs) -> str:
        f"""
        Extracts clean text content (without HTML tags, scripts, and styles) of a web page at the specified URL.

        Args:
            url: Full link of the web page.
            timeout: Page load timeout in seconds. Default is {SCRAPE_TIMEOUT}.
        """
        headers = {
            "User-Agent": USER_AGENT
        }
        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code != 200:
                    return f"Failed to load page, code: {resp.status_code}"
                soup = BeautifulSoup(resp.text, "html.parser")
                for script in soup(["script", "style"]):
                    script.decompose()
                text = soup.get_text(separator=" ", strip=True)
                return text[:SCRAPE_CHAR_LIMIT] + "..." if len(text) > SCRAPE_CHAR_LIMIT else text
        except Exception as e:
            return f"Web page parsing error: {str(e)}"

    # =====================================================================
    # CATEGORY 3: Telegram Automation (Telegram Automation Actions)
    # =====================================================================

    async def send_agent_message(self, text: str, chat_id: Any = None, reply_to_msg_id: int = None, reply_to_chat_id: Any = None, quote_text: str = None, is_deleted_fallback: bool = False, fallback_sender_name: str = "User", fallback_sender_id: int = None, **kwargs) -> str:
        """
        Sends a message, a standard reply, a cross-chat reply, or an quote reply.
        Use this tool as your primary way of sending any messages to avoid duplication.

        Args:
            text: Your actual reply or message content.
            chat_id: The target chat ID. Defaults to the current active chat.
            reply_to_msg_id: Optional message ID to reply to (works for same-chat and cross-chat).
            reply_to_chat_id: Required only if reply_to_msg_id is in another chat.
            quote_text: Optional snippet of the text you are quoting.
            is_deleted_fallback: Set to True if you are replying to a DELETED message.
                                This will format the message as an AyuGram-style blockquote.
            fallback_sender_name: Name of the sender for the deleted fallback quote.
            fallback_sender_id: Numerical ID of the sender for tg://user?id link.
        """
        if not client:
            return "Error: Telethon client is not initialized."

        # Resolve target chat ID from contextual variables if not specified
        if chat_id is None:
            try:
                chat_id = current_chat_id.get()
            except LookupError:
                return "Error: Failed to determine target chat."

        try:
            if isinstance(chat_id, str):
                try: 
                    chat_id = int(chat_id)
                except ValueError: 
                    pass

            # Scenario 1: Quoting of a deleted or unavailable message
            if is_deleted_fallback and quote_text:
                # Strip square brackets around media descriptors if any (e.g. "[Album]" -> "Album")
                clean_quote = quote_text.strip("[]")
                
                # Format sender name in bold with numerical profile link if available
                sender_link = f"[**{fallback_sender_name}**](tg://user?id={fallback_sender_id})" if fallback_sender_id else f"**{fallback_sender_name}**"
                
                # Start formatting the unified quote block with the author's name inside the blockquote
                formatted_quote = f"> {sender_link}\n"
                
                # Wrap each quote line with blockquote syntax '>'
                for line in clean_quote.split("\n"):
                    formatted_quote += f"> {line}\n"
                
                # Combine the formatted fallback quote block and the actual reply text
                final_text = f"{formatted_quote}\n{text}"
                result = await client.send_message(chat_id, final_text, parse_mode="markdown")
                
                # Synchronously write the outgoing message to the DB immediately to eliminate the race condition
                await db.save_message(str(chat_id), "model", final_text, msg_id=result.id)
                import bot
                bot.processed_msg_ids.add((int(chat_id), result.id))
                
                return f"Message sent successfully with AyuGram-style fallback quote. Message ID: {result.id}"

            # Scenario 2: Standard reply or cross-chat reply via Telegram API
            if reply_to_msg_id and (not reply_to_chat_id or str(reply_to_chat_id) == str(chat_id)) and not quote_text:
                # Same-chat standard reply without quotes uses plain integer message ID
                result = await client.send_message(
                    chat_id,
                    text,
                    reply_to=int(reply_to_msg_id),
                    **kwargs
                )
            else:
                # Cross-chat reply or quote requires constructing InputReplyToMessage
                from telethon.tl.functions.messages import SendMessageRequest
                from telethon.tl.types import InputReplyToMessage
                
                reply_peer = None
                if reply_to_chat_id and str(reply_to_chat_id) != str(chat_id):
                    try:
                        if isinstance(reply_to_chat_id, str):
                            try: 
                                reply_to_chat_id = int(reply_to_chat_id)
                            except ValueError: 
                                pass
                        reply_peer = await client.get_input_entity(reply_to_chat_id)
                    except Exception as peer_err:
                        logger.warning(f"Failed to get reply peer entity: {str(peer_err)}")

                reply_to_param = InputReplyToMessage(
                    reply_to_msg_id=int(reply_to_msg_id) if reply_to_msg_id else None,
                    reply_to_peer_id=reply_peer,
                    quote_text=quote_text
                )

                peer_entity = await client.get_input_entity(chat_id)
                request = SendMessageRequest(
                    peer=peer_entity,
                    message=text,
                    reply_to=reply_to_param,
                    **kwargs
                )
                result = await client(request)

            # Synchronously write the outgoing message to the DB immediately to eliminate the race condition
            await db.save_message(str(chat_id), "model", text, msg_id=result.id)
            import bot
            bot.processed_msg_ids.add((int(chat_id), result.id))

            # Warn the model if writing to the current chat to avoid duplicates
            cid = current_chat_id.get()
            if str(chat_id) == str(cid):
                return (
                    f"Success. Message delivered to current chat. Message ID: {result.id}.\n"
                    f"[STRICT WARNING TO AI]: This message has been sent to the chat. "
                    f"Please leave your standard response.text completely EMPTY or call the 'no_op_ignore' tool "
                    f"to finish the transaction without sending a duplicate message."
                )

            return f"Success. Message sent to chat {chat_id}. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending message: {str(e)}"

    async def execute_telegram_action(self, method_name: str, args_json: str, timeout: float = 60.0, wait_response_seconds: float = BOT_RESPONSE_TIMEOUT, **kwargs) -> str:
        """Calls helper asynchronous Telethon client methods or sends raw Telegram API requests."""
        if not client:
            return "Error: Telethon client is not initialized."
            
        if any(x in method_name.lower() for x in TELEGRAM_METHOD_BLACKLIST):
            return f"Calling method '{method_name}' is blocked by the security system."

        try:
            call_kwargs = json.loads(args_json) if args_json else {}
            if kwargs:
                call_kwargs.update(kwargs)

            # Auto-inject current chat_id if entity is missing in send_message/send_file
            if method_name in ["send_message", "send_file"] and "entity" not in call_kwargs:
                try:
                    call_kwargs["entity"] = current_chat_id.get()
                except Exception:
                    pass

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
                    if isinstance(entity, int):
                        target_id = entity
                    else:
                        target_entity = await client.get_entity(entity)
                        target_id = target_entity.id
                    
                    cid = current_chat_id.get()
                    if abs(int(target_id)) == abs(int(cid)):
                        call_kwargs["reply_to"] = current_reply_to_id.get()
                except Exception as e:
                    logger.debug(f"Failed to automatically substitute reply_to for {method_name}: {str(e)}")

            if method_name.startswith("functions."):
                async def auto_upload_files(data):
                    if isinstance(data, dict):
                        new_dict = {}
                        for k, v in data.items():
                            if isinstance(v, str) and os.path.isabs(v) and os.path.exists(v) and os.path.isfile(v):
                                uploaded_file_obj = await client.upload_file(v)
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
                    if isinstance(entity, int):
                        target_id = entity
                    else:
                        target_entity = await client.get_entity(entity)
                        target_id = target_entity.id
                    
                    cid = current_chat_id.get()
                    if abs(target_id) == abs(cid) or str(target_id) in str(cid) or str(cid) in str(target_id):
                        is_current_chat_send = True
            except Exception as check_ex:
                logger.debug(f"Duplicate check error in execute_telegram_action: {str(check_ex)}")

            result = None
            if method_name.startswith("functions."):
                parts = method_name.split(".")[1:]
                import telethon.functions as tf
                obj = tf
                for part in parts:
                    obj = getattr(obj, part)
                request = obj(**call_kwargs)
                result = await asyncio.wait_for(client(request), timeout=timeout)
            else:
                func = getattr(client, method_name, None)
                if not func:
                    return f"Method '{method_name}' not found in the Telethon client."
                
                call_res = func(**call_kwargs)
                if inspect.isawaitable(call_res):
                    result = await asyncio.wait_for(call_res, timeout=timeout)
                else:
                    result = call_res

            if is_current_chat_send:
                logger.info("AI successfully sent a message to the current chat via the tool. Returning instruction to the AI not to duplicate the response.")
                return (
                    f"Success. Action {method_name} executed. Result: {str(result)[:TELEGRAM_ACTION_CONFIRM_LIMIT]}.\n"
                    f"[WARNING TO AI]: This message has already been sent and delivered to the recipient in the current active chat. "
                    f"Please DO NOT duplicate this exact text in your normal reply (response.text) at the next step unless you explicitly need to. "
                    "If the dialogue is finished, simply call the 'no_op_ignore' tool and complete the transaction."
                )

            # Waiting for bot responses
            if method_name == "send_message" and "entity" in call_kwargs:
                entity = call_kwargs["entity"]
                is_target_bot = False
                try:
                    target_entity = await client.get_entity(entity)
                    is_target_bot = getattr(target_entity, "bot", False)
                except Exception:
                    if isinstance(entity, str) and entity.lower().endswith("bot"):
                        is_target_bot = True

                if is_target_bot and hasattr(result, "id"):
                    sent_msg_id = result.id
                    for _ in range(int(wait_response_seconds)):
                        await asyncio.sleep(1.0)
                        try:
                            history = await client.get_messages(entity, limit=1)
                            me = await client.get_me()
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
                                                try:
                                                    btn_info += f" (callback_data: '{btn.data.decode('utf-8')}')"
                                                except Exception:
                                                    btn_info += f" (callback_hex: '{btn.data.hex()}')"
                                            elif hasattr(btn, 'url') and btn.url:
                                                btn_info += f" (url: '{btn.url}')"
                                            row_btns.append(btn_info)
                                        buttons_text.append(" | ".join(row_btns))
                                
                                buttons_summary = ""
                                if buttons_text:
                                    buttons_summary = "\n[Inline buttons in the message from the bot]:\n" + "\n".join(buttons_text)
                                    
                                return (
                                    f"Message successfully delivered.\n"
                                    f"--- Instant reply from the bot (Message ID: {bot_reply.id}) ---\n"
                                    f"Text: {reply_text}\n{buttons_summary}"
                                )
                        except Exception as hist_err:
                            logger.error(f"Error receiving bot response: {str(hist_err)}")
                    
            from utils import safe_serialize
            serialized_res = safe_serialize(result)
            truncated_res = serialized_res[:5000] + "\n[Output truncated]" if len(serialized_res) > 5000 else serialized_res
            return f"Action {method_name} successfully executed. Result: {truncated_res}"
        except Exception as e:
            return f"Error executing '{method_name}': {str(e)}"

    async def send_inline_bot_result(self, bot_username: str, query: str, result_index: int = DEFAULT_RESULT_INDEX, chat_id: Any = None, **kwargs) -> str:
        f"""
        Performs an inline query to the specified external bot (e.g., 'pic', 'gif', 'youtube', 'like')
        and sends the selected result to the specified chat as a reply to the user's latest message.

        Args:
            bot_username: The username of the inline bot without the @ symbol (e.g., 'pic', 'gif', 'youtube', 'like').
            query: The search query for the inline bot (e.g., 'cats', 'lofi hip hop').
            result_index: The sequential index of the result in the list (0, 1, 2...). Default is {f"{DEFAULT_RESULT_INDEX} (the first result)" if DEFAULT_RESULT_INDEX == 0 else DEFAULT_RESULT_INDEX}.
            chat_id: Optional ID or username of the chat/channel to send the result to. If not specified, sends to the current chat.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        
        if chat_id is None:
            try:
                chat_id = current_chat_id.get()
            except LookupError:
                return "Error: Failed to determine the target chat."
                
        try:
            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass

            logger.info(f"Executing inline query to @{bot_username} with text '{query}'...")
            results = await client.inline_query(bot_username, query)
            if not results:
                return f"Inline bot @{bot_username} did not return any results for the query '{query}'."
                
            if result_index < 0 or result_index >= len(results):
                return f"Result index {result_index} out of range (total found: {len(results)})."
                
            # Automatic substitution of locked reply_to on inline result
            reply_to_id = None
            try:
                cid = current_chat_id.get()
                if abs(int(chat_id)) == abs(int(cid)):
                    reply_to_id = current_reply_to_id.get()
            except Exception as e:
                logger.debug(f"Failed to automatically get locked reply_to for inline click: {str(e)}")

            await results[result_index].click(chat_id, reply_to=reply_to_id, **kwargs)
            return f"Result under index {result_index} from bot @{bot_username} successfully sent to chat {chat_id}."
        except Exception as e:
            return f"Error executing inline query: {str(e)}"

    async def click_inline_button(self, chat_entity: Any, message_id: int, button_index: int = None, button_text: str = None, timeout: float = BUTTON_CLICK_TIMEOUT, **kwargs) -> str:
        f"""
        Clicks on an inline button in the specified message of another bot.

        Args:
            chat_entity: The username or ID of the chat/bot that sent the buttons.
            message_id: The message ID.
            button_index: The sequential index of the button to click (starting from 0).
            button_text: The exact text on the button.
            timeout: Button click timeout in seconds. Default is {BUTTON_CLICK_TIMEOUT}.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_entity, str):
                try:
                    chat_entity = int(chat_entity)
                except ValueError:
                    pass
            message = await asyncio.wait_for(client.get_messages(chat_entity, ids=message_id), timeout=timeout)
            if not message:
                return f"Message with ID {message_id} not found."
            if not message.reply_markup or not hasattr(message.reply_markup, 'rows'):
                return "There are no inline buttons in the message."

            buttons = []
            for row in message.reply_markup.rows:
                for btn in row.buttons:
                    buttons.append(btn)

            target_btn = None
            if button_text is not None:
                for btn in buttons:
                    if btn.text.strip().lower() == button_text.strip().lower():
                        target_btn = btn
                        break
                if not target_btn:
                    return f"Button with text '{button_text}' not found."
            elif button_index is not None:
                if 0 <= button_index < len(buttons):
                    target_btn = buttons[button_index]
                else:
                    return f"Button index {button_index} out of range."
            else:
                return "Specify button_index or button_text to click."

            await asyncio.wait_for(message.click(button=target_btn, **kwargs), timeout=timeout)
            return f"Button '{target_btn.text}' successfully clicked."
        except Exception as e:
            return f"Error clicking the button: {str(e)}"

    async def set_message_reaction(self, chat_id: Any, message_id: int, reaction_emoji: str = None, is_add: bool = True, **kwargs) -> str:
        """
        Sets or removes a reaction emoji (standard emoticon or premium custom emoji) on a specific message.

        Args:
            chat_id: The username or numerical ID of the target chat/channel.
            message_id: The ID of the message to react to.
            reaction_emoji: The reaction emoji string (e.g., '👍', '❤️'), or custom premium emoji document ID (numeric string), or None.
            is_add: True to add/set the reaction, False to remove/clear it. Default is True.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl import types as tl_types

            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass

            reaction_list = []
            if is_add and reaction_emoji:
                if str(reaction_emoji).isdigit():
                    reaction_list.append(tl_types.ReactionCustomEmoji(document_id=int(reaction_emoji)))
                else:
                    reaction_list.append(tl_types.ReactionEmoji(emoticon=reaction_emoji))

            await client(SendReactionRequest(
                peer=chat_id,
                msg_id=int(message_id),
                reaction=reaction_list
            ))
            return f"Success. Message #{message_id} reaction updated in chat {chat_id}."
        except Exception as e:
            return f"Error setting reaction: {str(e)}"

    async def send_telegram_media(self, chat_id: Any, media_id: str, access_hash: str, file_reference_hex: str, media_type: str, caption: str = None, reply_to_msg_id: int = None, **kwargs) -> str:
        """
        Sends any cached or identified Telegram media (sticker, photo, document, voice message, video note)
        using its exact raw MTProto identification metadata.

        Args:
            chat_id: The username or numerical ID of the target chat/channel.
            media_id: The numerical ID of the document or photo.
            access_hash: The numerical access hash of the document or photo.
            file_reference_hex: The hex-encoded file reference string.
            media_type: The type of the media ('photo', 'sticker', 'voice', 'video_note', 'document').
            caption: Optional text caption (not applicable for stickers).
            reply_to_msg_id: Optional message ID to reply to.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        try:
            from telethon.tl import types as tl_types

            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass

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

            result = await client.send_file(
                chat_id,
                file=media_obj,
                caption=caption,
                reply_to=reply_to_param
            )
            return f"Success. Media sent successfully to chat {chat_id}. Message ID: {result.id}"
        except Exception as e:
            return f"Error sending media: {str(e)}"

    # =====================================================================
    # CATEGORY 4: Timers and Scheduler (SQLite Schedulers)
    # =====================================================================

    def set_task_timer(self, delay_seconds: int, action_description: str, code_to_execute: str = None, **kwargs) -> str:
        """Schedules a task or asynchronous Python code execution by timer."""
        if not db:
            return "Error: Database is not initialized."
        cid = current_chat_id.get()
        asyncio.create_task(db.add_timer(cid, delay_seconds, action_description, code_to_execute))
        return f"Timer successfully set for {delay_seconds} seconds and saved to the DB."


    async def delete_task_timer(self, timer_id: int, **kwargs) -> str:
        """Deletes a scheduled timer from the database by its unique ID."""
        if not db:
            return "Error: Database is not initialized."
        try:
            await db.delete_timer(timer_id)
            return f"Success. Timer ID {timer_id} cancelled."
        except Exception as e:
            return f"Error deleting timer: {str(e)}"


    async def list_task_timers(self, **kwargs) -> str:
        """Returns a formatted list of all scheduled timers."""
        if not db:
            return "Error: Database is not initialized."
        try:
            timers = await db.get_pending_timers()
            if not timers:
                return "No scheduled timers."
            
            lines = []
            import time
            now = int(time.time())
            for t_id, chat_id, execute_at, action, code in timers:
                remaining = execute_at - now
                code_ref = "yes" if code else "no"
                lines.append(f"ID {t_id} | Chat {chat_id} | Will trigger in {remaining} sec | Task: '{action}' | Auto-code: {code_ref}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"


    # =====================================================================
    # CATEGORY 5: Triggers and Auto-Wake (Wake Triggers)
    # =====================================================================

    async def set_wake_trigger(self, trigger_type: str, trigger_value: str, action_description: str, code_to_execute: str = None, **kwargs) -> str:
        """Sets an automatic wake trigger in the current chat."""
        if not db:
            return "Error: Database is not initialized."
        cid = current_chat_id.get()
        try:
            await db.add_trigger(cid, trigger_type, trigger_value, action_description, code_to_execute)
            return f"Trigger '{trigger_type}' with value '{trigger_value}' successfully set."
        except Exception as e:
            return f"Error saving trigger: {str(e)}"


    async def delete_wake_trigger(self, trigger_id: int, **kwargs) -> str:
        """Deletes an active wake trigger from the SQLite database by its ID."""
        if not db:
            return "Error: Database is not initialized."
        try:
            await db.delete_trigger(trigger_id)
            return f"Success. Trigger ID {trigger_id} deleted."
        except Exception as e:
            return f"Error deleting trigger: {str(e)}"


    async def list_task_triggers(self, **kwargs) -> str:
        """Returns a formatted text list of all active wake triggers for the current chat."""
        if not db:
            return "Error: Database is not initialized."
        try:
            cid = current_chat_id.get()
            triggers = await db.get_active_triggers(cid)
            if not triggers:
                return "There are no active wake triggers for this chat."
            
            lines = []
            for t_id, t_type, t_val, t_action, t_code in triggers:
                code_ref = "yes" if t_code else "no"
                lines.append(f"ID {t_id} | Type: '{t_type}' | Value: '{t_val}' | Task: '{t_action}' | Auto-code: {code_ref}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"


    # =====================================================================
    # CATEGORY 6: Multimedia and Generative AI (Generative Multimedia AI)
    # =====================================================================

    async def generate_image(self, prompt: str, model: str = DEFAULT_IMAGE_MODEL, width: int = DEFAULT_IMAGE_WIDTH, height: int = DEFAULT_IMAGE_HEIGHT, seed: int = -1, reference_image_url: str = None, timeout: float = GENERATE_IMAGE_TIMEOUT, **kwargs) -> str:
        """
        Generates high-quality images from a text description on Pollinations.ai.
        Completely free, supports any additional parameters in kwargs (e.g., aspectRatio, negative_prompt, enhance).
        Some models, such as 'flux' and 'zimage', have no censorship.

        Available models (model, source https://gen.pollinations.ai/image/models):
        - 'flux' (FLUX.1 Schnell) — ultra-fast versatile photorealistic model.
        - 'zimage' (Alibaba S3-DiT 6B) — anime, art, high speed, and high-quality SPAN upscaling.
        - 'grok-imagine' (xAI) — official photorealistic model from Elon Musk.
        - 'grok-imagine-pro' (xAI Aurora) — premium version of Grok for ultra-detailed images.
        - 'klein' (FLUX.2 Klein 4B) — lightweight, fast, and plastic model.
        - 'nanobanana-2' — Google Gemini 3.1 Flash Image. Sharp details, strong prompt following (paid).
        - 'nanobanana-pro' — Google Gemini 3 Pro Image (Thinking, 4K resolution, deep understanding).
        - 'seedream' (ByteDance 4.0) — high-level realism (paid).
        - 'seedream-pro' (ByteDance 4.5 Pro) — premium-class photorealism with highest accuracy (paid).
        """
        import random
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://gen.pollinations.ai/image/{encoded_prompt}"
        
        params = {
            "model": model,
            "width": width,
            "height": height,
            "nologo": "true",
            "private": "true",
            "safe": "false"
        }
        
        if seed != -1:
            params["seed"] = seed
        else:
            params["seed"] = random.randint(1, 999999999)
            
        if "nanobanana" in model:
            params["reasoning"] = "pro"
            
        if reference_image_url:
            params["referenceImage"] = reference_image_url

        if kwargs:
            params.update(kwargs)

        try:
            resp = await call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Pollinations AI error: status {resp.status_code}"
                
            image_bytes = resp.content
            out_filename = DEFAULT_IMAGE_NAME
            out_path = WORKSPACE_DIR / out_filename
            with open(out_path, "wb") as f:
                f.write(image_bytes)
                
            cid = current_chat_id.get()
            return (
                f"Image generated and saved as '{out_filename}'.\n"
                f"To send the file, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"caption\": \"Your prompt\"}}')"
            )
        except Exception as e:
            return f"Image generation error: {str(e)}"


    async def generate_audio(self, prompt: str, voice: str = DEFAULT_AUDIO_VOICE, model: str = DEFAULT_AUDIO_MODEL, timeout: float = GENERATE_AUDIO_TIMEOUT, **kwargs) -> str:
        """
        Synthesizes (generates) high-quality speech (audio message) or music from a text description on Pollinations.ai.
        Completely free, supports any additional parameters in kwargs (e.g., lyrics, tempo, audio_output).

        Available models (model, source https://gen.pollinations.ai/audio/models):
        - 'qwen-tts-instruct' — excellent TTS with support for emotion, intonation, and style control.
        - 'qwen-tts' — fast, multilingual, and high-quality speech synthesis.
        - 'elevenflash' (ElevenLabs v2.5) — high-quality low-latency TTS (~75ms, 32 languages, paid).
        - 'elevenlabs' (ElevenLabs v3) — premium synthesis with expressive emotions and audio tag markup (paid).
        - 'elevenmusic' — studio-quality music generation from a prompt (paid).
        - 'acestep' (ACE-Step 1.5) — fast music generation with lyrics support.
        """
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://gen.pollinations.ai/audio/{encoded_prompt}"
        
        params = {
            "voice": voice,
            "model": model,
            "response_format": "mp3"
        }
        
        if kwargs:
            params.update(kwargs)
        
        try:
            logger.info("Launching audio synthesis...")
            resp = await call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Audio generation error: status {resp.status_code}"
            
            audio_bytes = resp.content
            out_filename = DEFAULT_AUDIO_NAME
            with open(WORKSPACE_DIR / out_filename, "wb") as f:
                f.write(audio_bytes)
                
            cid = current_chat_id.get()
            return (
                f"Speech synthesized and saved as '{out_filename}'.\n"
                f"To send, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"voice\": true}}')"
            )
        except Exception as e:
            return f"Audio synthesis error: {str(e)}"


    async def generate_video(self, prompt: str, model: str = DEFAULT_VIDEO_MODEL, duration: int = DEFAULT_VIDEO_DURATION, aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO, seed: int = -1, timeout: float = GENERATE_VIDEO_TIMEOUT, **kwargs) -> str:
        """
        Generates a short video animation from a text description on Pollinations.ai.
        Completely free, supports any additional parameters in kwargs (e.g., start_frame, audio_output).

        Available models (model, source https://gen.pollinations.ai/image/models):
        - 'wan' (Alibaba Wan 2.6) — high-quality video generation up to 1080P with native sound (2-15 sec).
        - 'wan-fast' (Alibaba Wan 2.2) — ultra-fast and cost-effective video generation (5 sec, 480P).
        - 'wan-pro' (Alibaba Wan 2.7) — professional Alibaba model with built-in background music generation (720P/1080P, paid).
        - 'ltx-2' (Lightricks 2.3) — fast and smooth camera dynamics with an integrated smart upscaler.
        - 'grok-video-pro' (xAI) — official video generator from Elon Musk (720p, 1-15 sec, paid).
        - 'seedance-2.0' (ByteDance) — complex motion physics and native audio (720p, paid).
        - 'veo' (Google Veo 3.1) — cinematic realism, high depth of field (paid).
        """
        import random
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://gen.pollinations.ai/video/{encoded_prompt}"
        
        params = {
            "model": model,
            "duration": duration,
            "aspectRatio": aspect_ratio
        }
        if seed != -1:
            params["seed"] = seed
        else:
            params["seed"] = random.randint(1, 999999999)
            
        if kwargs:
            params.update(kwargs)
            
        try:
            logger.info("Launching video generation...")
            resp = await call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Video generation error: status {resp.status_code}"
                
            video_bytes = resp.content
            out_filename = DEFAULT_VIDEO_NAME
            with open(WORKSPACE_DIR / out_filename, "wb") as f:
                f.write(video_bytes)
                
            cid = current_chat_id.get()
            return (
                f"Video clip generated and saved as '{out_filename}'.\n"
                f"To send, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"caption\": \"Your video\"}}')"
            )
        except Exception as e:
            return f"Video generation error: {str(e)}"


    async def upload_file_to_google(self, filename: str, timeout: float = GOOGLE_UPLOAD_TIMEOUT, **kwargs) -> dict:
        f"""
        Uploads the specified file from the sandbox (bot_workspace) to remote Google Gemini API cloud servers.
        Returns a structured dictionary with metadata of the uploaded file.
        Used when the file is too large to be passed directly into the prompt or when working with heavy documents/videos.

        Args:
            filename: The name of the file in the sandbox (e.g., the default generated image file or any other file).
            timeout: File upload timeout limit in seconds. Default is {GOOGLE_UPLOAD_TIMEOUT}.
        """
        if not key_manager:
            return {"status": "error", "message": "Error: Gemini KeyManager is not initialized."}
            
        file_path = WORKSPACE_DIR / os.path.basename(filename)
        if not file_path.exists() or not file_path.is_file():
            return {"status": "error", "message": f"Error: File '{filename}' not found."}

        try:
            logger.info("Uploading file to Google servers...")
            gemini_client = key_manager.get_client()
            # Upload the file to Google servers
            uploaded_file = await asyncio.wait_for(gemini_client.aio.files.upload(file=str(file_path.resolve())), timeout=timeout)
            
            # Save Google URI -> mime_type mapping to SQLite database
            if db:
                await db.set_memory(uploaded_file.uri, uploaded_file.mime_type)
                
            logger.info(f"File '{filename}' successfully uploaded to Google servers. URI: {uploaded_file.uri}")
            return {
                "status": "success",
                "filename": filename,
                "google_uri": uploaded_file.uri,
                "mime_type": uploaded_file.mime_type,
                "message": f"File {filename} successfully uploaded to Google Gemini servers. It is automatically attached to this transaction, you can analyze it right now."
            }
        except Exception as e:
            return {"status": "error", "message": f"Error uploading to Google: {str(e)}"}


    async def upload_file_to_public_host(self, filename: str, provider: str = DEFAULT_PUBLIC_UPLOAD_PROVIDER, timeout: float = PUBLIC_UPLOAD_TIMEOUT, **kwargs) -> str:
        f"""
        Uploads a media file or document from the local AI sandbox to the free anonymous cloud Telegraph, file.io, Uguu.se,
        or the native secure media storage PollinationsAI.

        Available providers (provider):
        - 'auto' — automatically selects the best available hosting for the current file type.
        - 'pollinations' — uploads the file to the native PollinationsAI media storage with authorization via your current API key (Bearer token).
          Returns a long-lived link like https://media.pollinations.ai/<hash>, perfectly compatible with all models.
        - 'telegraph' — uploads only images or short mp4s to the anonymous Telegra.ph cloud.
        - 'file.io' — uploads any files to the temporary file.io hosting.
        - 'uguu.se' — uploads any temporary files to the Uguu.se hosting.

        Args:
            filename: The name of the file in the local folder (e.g., the default generated image file).
            provider: The name of the selected provider ('auto', 'pollinations', 'telegraph', 'file.io', 'uguu.se'). Default is {DEFAULT_PUBLIC_UPLOAD_PROVIDER}.
            timeout: Network request execution timeout limit in seconds. Default is {PUBLIC_UPLOAD_TIMEOUT}.
        """
        import time
        from PIL import Image

        if not client:
            return "Error: Telethon client is not initialized."
            
        file_path = WORKSPACE_DIR / os.path.basename(filename)
        if not file_path.exists() or not file_path.is_file():
            return f"Error: File '{filename}' not found in local storage."

        ext = filename.split('.')[-1].lower()
        supported_exts = {
            'gif': 'image/gif', 
            'jpeg': 'image/jpeg', 
            'jpg': 'image/jpeg', 
            'png': 'image/png', 
            'mp4': 'video/mp4'
        }

        headers = {
            "User-Agent": USER_AGENT
        }

        temp_jpg_path = None

        try:
            # Automatic conversion of transparent PNGs for Telegraph
            if ext == "png" and provider in ["telegraph", "auto"]:
                try:
                    logger.info("PNG detected. Starting conversion to JPEG to bypass alpha channel limitations...")
                    img = Image.open(file_path)
                    
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        background = Image.new("RGB", img.size, (255, 255, 255))
                        mask = img.split()[3] if img.mode == "RGBA" else img.split()[1] if img.mode == "LA" else None
                        background.paste(img, mask=mask)
                        img = background
                    else:
                        img = img.convert("RGB")
                    
                    temp_jpg_path = file_path.with_name(f"temp_upload_{int(time.time())}.jpg")
                    img.save(temp_jpg_path, "JPEG", quality=95)
                    
                    file_path = temp_jpg_path
                    ext = "jpg"
                    filename = temp_jpg_path.name
                    logger.info(f"Conversion completed. Temporary file: {temp_jpg_path}")
                except Exception as conv_err:
                    logger.warning(f"Failed to convert PNG: {str(conv_err)}")

            # 1. Attempt upload to PollinationsAI (if 'pollinations' or 'auto' is selected)
            if provider in ["pollinations", "auto"]:
                try:
                    logger.info("Uploading local file to PollinationsAI media storage...")
                    current_key = await pollinations_key_manager.get_active_key() if pollinations_key_manager else ""
                    
                    url = "https://gen.pollinations.ai/upload"
                    headers_auth = headers.copy()
                    if current_key:
                        headers_auth["Authorization"] = f"Bearer {current_key}"
                        
                    with open(file_path, "rb") as f:
                        files = {"file": (os.path.basename(file_path), f)}
                        async with httpx.AsyncClient(timeout=timeout) as client_httpx:
                            resp = await client_httpx.post(url, files=files, headers=headers_auth)
                            if resp.status_code == 200:
                                res_json = resp.json()
                                public_url = res_json.get("url")
                                if public_url:
                                    logger.info(f"File successfully uploaded to PollinationsAI: {public_url}")
                                    return (
                                        f"File '{filename}' successfully uploaded to PollinationsAI cloud media storage!\n"
                                        f"Public URL: {public_url}\n"
                                        f"You can pass this URL to the parameters of other generators!"
                                    )
                except Exception as e:
                    logger.warning(f"Upload to PollinationsAI failed: {str(e)}. Trying other options...")
                    if provider == "pollinations":
                        return f"Error: Provider 'pollinations' returned an error: {str(e)}"

            # 2. Attempt upload to Telegraph
            if provider in ["telegraph", "auto"] and ext in supported_exts:
                url = "https://telegra.ph/upload"
                try:
                    logger.info(f"Uploading local file '{filename}' to Telegraph...")
                    with open(file_path, "rb") as f:
                        files = {"file": ("file", f, supported_exts[ext])}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                            resp = await client_httpx.post(url, files=files, headers=headers)
                            
                            if resp.status_code == 200:
                                data = resp.json()
                                if isinstance(data, list) and len(data) > 0 and "src" in data[0]:
                                    public_url = "https://telegra.ph" + data[0]["src"]
                                    logger.info(f"File '{filename}' successfully uploaded to Telegraph: {public_url}")
                                    return (
                                        f"File '{filename}' successfully uploaded to the anonymous Telegraph cloud!\n"
                                        f"Public URL: {public_url}\n"
                                        f"You can pass this URL to the 'reference_image_url' parameter of the 'generate_image' function for style transfer (Image-to-Image)!"
                                    )
                            else:
                                logger.warning(f"Telegraph returned status {resp.status_code}. Switching to file.io...")
                except Exception as e:
                    logger.warning(f"Upload attempt to Telegraph failed: {str(e)}. Trying file.io...")

            # 3. Attempt upload to file.io
            if provider in ["file.io", "auto"]:
                try:
                    logger.info(f"Uploading local file '{filename}' to developer hosting file.io...")
                    with open(file_path, "rb") as f:
                        files = {"file": (os.path.basename(file_path), f)}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client_httpx:
                            resp = await client_httpx.post("https://file.io", files=files, headers=headers)
                            
                            if resp.status_code == 200:
                                res_json = resp.json()
                                if res_json.get("success"):
                                    public_url = res_json.get("link")
                                    logger.info(f"File '{filename}' successfully uploaded to file.io: {public_url}")
                                    return (
                                        f"File '{filename}' successfully uploaded to the developer hosting file.io!\n"
                                        f"Public URL: {public_url}\n"
                                        f"You can pass this URL to the 'reference_image_url' parameter of the 'generate_image' function for style transfer (Image-to-Image)!"
                                    )
                            else:
                                logger.warning(f"file.io returned status {resp.status_code}. Switching to Uguu.se...")
                except Exception as e:
                    logger.warning(f"Upload to file.io failed: {str(e)}. Trying Uguu.se...")

            # 4. Attempt upload to Uguu.se
            if provider in ["uguu.se", "auto"]:
                try:
                    logger.info(f"Uploading local file '{filename}' to open hosting Uguu.se...")
                    with open(file_path, "rb") as f:
                        files = {"files[]": (os.path.basename(file_path), f, f"image/{ext}" if ext in ["jpg", "jpeg", "png"] else "application/octet-stream")}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                            resp = await client_httpx.post("https://uguu.se/upload", files=files, headers=headers)
                            
                            if resp.status_code == 200:
                                res_json = resp.json()
                                if res_json.get("success") and res_json.get("files"):
                                    public_url = res_json["files"][0]["url"]
                                    logger.info(f"File successfully uploaded to Uguu.se: {public_url}")
                                    return (
                                        f"File '{filename}' successfully uploaded to the backup hosting Uguu.se!\n"
                                        f"Public URL: {public_url}\n"
                                        f"You can pass this URL to the 'reference_image_url' parameter of the 'generate_image' function for style transfer (Image-to-Image)!"
                                    )
                            else:
                                logger.error(f"Uguu.se returned status {resp.status_code}, body: {resp.text}")
                except Exception as e:
                    logger.error(f"Upload to Uguu.se failed: {str(e)}")

            return (
                f"Critical failure of all available hostings.\n"
                f"- PollinationsAI: Connection failure\n"
                f"- Telegraph: Error 400\n"
                f"- file.io: Connection failure\n"
                f"- Uguu.se: Connection failure"
            )

        finally:
            if temp_jpg_path and temp_jpg_path.exists():
                try:
                    temp_jpg_path.unlink()
                    logger.info(f"Temporary conversion file {temp_jpg_path} successfully deleted.")
                except Exception as clean_err:
                    logger.error(f"Failed to delete temporary file {temp_jpg_path}: {str(clean_err)}")


    # =====================================================================
    # CATEGORY 7: System Control, DB & Sandboxed VM (System Control, DB & Sandboxed VM)
    # =====================================================================

    # Dynamic creation/updating of custom tools at runtime
    async def create_or_update_custom_tool(self, name: str, category: str, description: str, code: str, **kwargs) -> str:
        """
        Creates a new or updates an existing custom dynamic AI tool at runtime.
        The tool is automatically saved to the SQLite database and instantly compiled/registered in memory,
        making it available for you to call immediately on the next generation step and persisting after the bot restarts.

        TOOL CODE WRITING RULES:
        1. The code must be valid, clean, and asynchronous Python code (use async def).
        2. The code must contain ONE main function whose name strictly matches the 'name' parameter (e.g., async def my_tool_name(arg1, **kwargs)).
        3. The function must accept the '**kwargs' parameter at the end of its signature to absorb any extra arguments passed.
        4. Inside the function, the following global objects are available to you:
           - 'client': Telethon proxy client for sending messages, files, or inline queries.
           - 'db': SQLite database manager (DBManager) for saving shared memory.
           - 'ai_manager': AI core control module (GeminiManager).
           - 'logger': logger object for outputting debug messages to the console.
        5. Standard libraries are available: httpx, json, asyncio, pathlib, urllib, os.

        Args:
            name: A unique name for the function being created in English in snake_case format (e.g., 'calculate_fibonacci').
            category: The category the tool belongs to. Choose from existing ones or create a new one.
            description: Detailed description and docstring instruction for the AI, explaining the purpose of the arguments and the tool's behavior.
            code: Full source code of the function as a text string.
        """
        if not db:
            return "Error: Database is not initialized."
        try:
            # 1. Write to DB
            await db.save_custom_tool(name, category, description, code)
            
            # 2. Safely compile the function code from the string
            from registry import compile_custom_tool, registry
            compiled_func = compile_custom_tool(name, code)
            
            # 3. Register in the global RAM of FunctionRegistry
            registry.register(
                name=name,
                callable_func=compiled_func,
                category=category,
                description=description,
                is_custom=True
            )
            return f"Success. Custom tool '{name}' created/updated, compiled in memory, and saved to the DB. It is immediately available for you to call!"
        except Exception as e:
            return f"Error creating/compiling custom tool '{name}': {str(e)}"

    # Deleting custom dynamic tools
    async def delete_custom_tool(self, name: str, **kwargs) -> str:
        """
        Deletes a previously created custom dynamic AI tool from the database and the registry's RAM.
        WARNING: Deletion of system (root) tools embedded in the bot core is strictly blocked for security reasons.

        Args:
            name: Unique name of the custom tool to be deleted.
        """
        if not db:
            return "Error: Database is not initialized."
            
        # Protection of system root tools from deletion by the AI model
        if name in ROOT_TOOL_CATEGORIES:
            return f"Error: Tool '{name}' is a system (root) tool of the bot core. Its deletion is strictly blocked for security reasons."
            
        try:
            # 1. Delete from DB
            deleted = await db.delete_custom_tool(name)
            if not deleted:
                return f"Error: Custom tool '{name}' not found in the database."
                
            # 2. Delete from FunctionRegistry RAM
            from registry import registry
            registry.unregister(name)
            return f"Success. Custom tool '{name}' completely deleted from the DB and RAM."
        except Exception as e:
            return f"Error deleting tool '{name}': {str(e)}"


# Mapping of root tools to their cohesive categories for automatic self-registration
    async def execute_python_code(self, code: str, **kwargs) -> str:
        """
        Executes asynchronous Python code in a safe isolated sandbox VM and returns the result.
        The code is executed relative to the local workspace folder.

        Args:
            code: The Python code to run. Must be asynchronous (e.g., 'await client.send_message(...)').
        """
        from sandbox import AsyncSandbox
        try:
            cid = current_chat_id.get()
        except LookupError:
            cid = None
            
        sandbox = AsyncSandbox(
            workspace_dir=WORKSPACE_DIR,
            client_instance=client,
            db_instance=db,
            ai_manager_instance=ai_manager,
            chat_id=cid
        )
        return await sandbox.execute(code)

    def no_op_ignore(self, reason: str, **kwargs) -> str:
        """
        Finishes the current generation step immediately without sending any text messages to the chat.
        Used when the incoming message is spam, flood, or does not require an answer.

        Args:
            reason: Explanation of why the conversation is being ignored.
        """
        logger.info(f"Dialogue ignored. Reason: {reason}")
        return f"Dialogue successfully ignored. Reason: {reason}"

    async def execute_sql_query(self, sql: str, **kwargs) -> str:
        """
        Executes a raw SQL query on the configured local SQLite database.
        Allows both data retrieval (SELECT) and database modifications (INSERT, UPDATE, DELETE, CREATE, DROP).

        Args:
            sql: The SQL query string to execute.
        """
        if not db:
            return "Error: Database is not initialized."
        try:
            async with db.db.execute(sql) as cursor:
                if cursor.description is not None:
                    # It is a data retrieval query (SELECT)
                    rows = await cursor.fetchall()
                    cols = [d[0] for d in cursor.description]
                    results = [dict(zip(cols, row)) for row in rows[:SQL_SELECT_LIMIT]]
                    if not results:
                        return "Query executed. No matching rows found."
                    
                    from utils import safe_serialize
                    out = safe_serialize(results)
                    return out[:SQL_STDOUT_CHAR_LIMIT] + "\n[Output truncated]" if len(out) > SQL_STDOUT_CHAR_LIMIT else out
                else:
                    # It is a data modification query (INSERT, UPDATE, DELETE, CREATE, etc.)
                    await db.db.commit()
                    rowcount = cursor.rowcount
                    lastrowid = cursor.lastrowid
                    res_parts = ["Query executed successfully. Transaction committed."]
                    if rowcount is not None and rowcount >= 0:
                        res_parts.append(f"Affected rows: {rowcount}")
                    if lastrowid is not None and lastrowid > 0:
                        res_parts.append(f"Last inserted row ID: {lastrowid}")
                    return "\n".join(res_parts)
        except Exception as e:
            return f"SQL Error: {str(e)}"

    async def run_sandboxed_command(self, command: str, **kwargs) -> str:
        """
        Runs a standard system bash/shell command securely in the sandbox and returns its stdout.

        Args:
            command: Bash shell command (e.g., 'ls -l' or 'du -sh *').
        """
        if FORBIDDEN_SHELL_REGEX.search(command):
            return "Security error: This shell command contains blocked terms or tries to access forbidden system files."
            
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(WORKSPACE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            res = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
            return res[:SANDBOX_COMMAND_CHAR_LIMIT] if len(res) > SANDBOX_COMMAND_CHAR_LIMIT else res if res else "Command finished with no output."
        except Exception as e:
            return f"Error executing shell command: {str(e)}"

    async def get_chat_history_from_db(self, chat_id: Any, limit: int = 50, **kwargs) -> str:
        """
        Retrieves raw historical messages from the SQLite database for a specific chat.

        Args:
            chat_id: The target chat ID or username.
            limit: Maximum number of messages to fetch (default is 50).
        """
        if not db:
            return "Error: Database is not initialized."
        try:
            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass
                    
            async with db.db.execute(
                "SELECT role, text, timestamp FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (str(chat_id), limit)
            ) as cursor:
                rows = await cursor.fetchall()
                
            if not rows:
                return f"No message history found in the DB for chat {chat_id}."
                
            rows.reverse()
            lines = []
            for role, text, ts in rows:
                lines.append(f"[{ts}] {role.upper()}: {text}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading chat history: {str(e)}"

    async def get_telegram_object_info(self, entity_id: Any, **kwargs) -> str:
        """
        Requests and returns comprehensive properties, names, bios, types, bot status, and membership of any Telegram user, group, or channel.
        Exposes 100% of the raw MTProto API fields by appending the full raw JSON payload to a clean visual summary.

        Args:
            entity_id: The username (e.g., 'durov') or numerical ID of the target user, bot, group, or channel.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(entity_id, str):
                try:
                    entity_id = int(entity_id)
                except ValueError:
                    pass
                    
            entity = await client.get_entity(entity_id)
            e_type = type(entity).__name__
            
            # 1. Visual Summary
            details = [
                f"Entity Details:",
                f"- ID: {entity.id}",
                f"- Type: {e_type}"
            ]
            
            if hasattr(entity, "username") and entity.username:
                details.append(f"- Username: @{entity.username}")
                
            bio_ref = "None"
            
            if e_type == "User":
                details.append(f"- First Name: '{getattr(entity, 'first_name', '') or ''}'")
                details.append(f"- Last Name: '{getattr(entity, 'last_name', '') or ''}'")
                
                # Highlight Bot Status - very important for AI context!
                is_bot = getattr(entity, 'bot', False)
                details.append(f"- IS BOT: {'Yes (This is a Telegram Bot)' if is_bot else 'No (This is a human user)'}")
                
                details.append(f"- Is Premium: {'Yes' if getattr(entity, 'premium', False) else 'No'}")
                details.append(f"- Is Verified: {'Yes' if getattr(entity, 'verified', False) else 'No'}")
                details.append(f"- Is Scam: {'Yes' if getattr(entity, 'scam', False) else 'No'}")
                details.append(f"- Is Fake: {'Yes' if getattr(entity, 'fake', False) else 'No'}")
                if getattr(entity, "phone", None):
                    details.append(f"- Phone: {entity.phone}")
                    
                meta = await db.get_user_meta(str(entity.id)) if db else None
                if meta:
                    bio_ref = meta.get("bio") or "None"
                    
            elif e_type in ["Channel", "Chat"]:
                details.append(f"- Title: '{getattr(entity, 'title', '') or ''}'")
                is_group = getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False) or e_type == "Chat"
                details.append(f"- Subtype: {'Supergroup/Group' if is_group else 'Broadcast Channel'}")
                details.append(f"- Is Verified: {'Yes' if getattr(entity, 'verified', False) else 'No'}")
                details.append(f"- Is Scam: {'Yes' if getattr(entity, 'scam', False) else 'No'}")
                details.append(f"- Is Fake: {'Yes' if getattr(entity, 'fake', False) else 'No'}")
                
                meta = await db.get_chat_meta(str(entity.id)) if db else None
                if meta:
                    bio_ref = meta.get("bio") or meta.get("description") or "None"
                    
            details.append(f"- Bio/Description from cache: '{bio_ref}'")
            summary_text = "\n".join(details)
            
            # 2. Complete Raw MTProto JSON Payload
            from utils import safe_serialize
            raw_json = safe_serialize(entity)
            
            return (
                f"{summary_text}\n\n"
                f"=== Raw MTProto API Payload (JSON) ===\n"
                f"{raw_json}"
            )
        except Exception as e:
            return f"Error retrieving Telegram object info: {str(e)}"

    async def get_telegram_message_details(self, chat_id: Any, message_id: int, **kwargs) -> str:
        """
        Requests and returns the complete properties, text, sender, formatting entities,
        reactions, edit history, views, forwards, attached media, and inline button layout of a specific message.
        Exposes 100% of the raw MTProto API fields (including forwards, inline bots, dice/game values, cross-chat replies, and formatting entities)
        by appending the raw JSON payload to a clean visual summary.

        Args:
            chat_id: Username or numerical ID of the chat/channel.
            message_id: The numerical ID of the message.
        """
        if not client:
            return "Error: Telethon client is not initialized."
        try:
            if isinstance(chat_id, str):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    pass
                    
            message = await client.get_messages(chat_id, ids=message_id)
            if not message:
                return f"Error: Message #{message_id} not found in chat {chat_id}."
                
            from parser import parse_sender_info, get_media_type_description
            sender_info = parse_sender_info(message.sender, message)
            
            # 1. Visual Summary
            details = [
                f"Message #{message.id} Properties:",
                f"- Sender: {sender_info}",
                f"- Date Sent: {message.date}",
                f"- Last Edited: {message.edit_date or 'Never edited'}",
                f"- Raw Text Content: '{message.message or ''}'"
            ]
            
            # Views and forwards (mainly channel specific)
            if getattr(message, 'views', None) is not None:
                details.append(f"- Views Count: {message.views}")
            if getattr(message, 'forwards', None) is not None:
                details.append(f"- Forwards Count: {message.forwards}")
                
            # Reply information
            if message.is_reply:
                details.append(f"- Is Reply To Message ID: {message.reply_to.reply_to_msg_id}")
                if getattr(message.reply_to, "quote_text", None):
                    details.append(f"- Quoted Text Fragment: '{message.reply_to.quote_text}'")
                    
            # Formatting entities
            entities_list = []
            if message.entities:
                for ent in message.entities:
                    ent_type = type(ent).__name__
                    entities_list.append(ent_type)
            details.append(f"- Text Formatting Entities: {', '.join(entities_list) if entities_list else 'None'}")
            
            # Attached Media details
            media_desc = "None"
            if message.media:
                m_desc = get_media_type_description(message)
                media_desc = f"{m_desc or 'Unknown Attachment'} ({type(message.media).__name__})"
            details.append(f"- Attached Media: {media_desc}")
            
            # Reactions
            reactions_list = []
            if getattr(message, 'reactions', None) and getattr(message.reactions, 'results', None):
                for r in message.reactions.results:
                    emoji = getattr(r.reaction, 'emoticon', None)
                    if not emoji and hasattr(r.reaction, 'document_id'):
                        emoji = f"[Custom Emoji ID: {r.reaction.document_id}]"
                    reactions_list.append(f"'{emoji or 'Unknown'}' (x{r.count})")
            details.append(f"- Reactions: {', '.join(reactions_list) if reactions_list else 'None'}")
            
            # Inline button layout (reply_markup)
            buttons_desc = []
            if message.reply_markup and hasattr(message.reply_markup, 'rows'):
                for r_idx, row in enumerate(message.reply_markup.rows):
                    row_btns = []
                    for b_idx, btn in enumerate(row.buttons):
                        btn_desc = f"Button [Index: {b_idx} in Row: {r_idx}] | Text: '{btn.text}'"
                        if hasattr(btn, 'data') and btn.data:
                            try:
                                btn_desc += f" | callback_data: '{btn.data.decode('utf-8')}'"
                            except Exception:
                                btn_desc += f" | callback_hex: '{btn.data.hex()}'"
                        elif hasattr(btn, 'url') and btn.url:
                             btn_desc += f" | URL: '{btn.url}'"
                        row_btns.append(btn_desc)
                    buttons_desc.append(f"Row {r_idx}:\n  " + "\n  ".join(row_btns))
                    
            buttons_summary = "No inline buttons."
            if buttons_desc:
                buttons_summary = "Inline Buttons Layout:\n" + "\n".join(buttons_desc)
            details.append(f"- {buttons_summary}")
            
            summary_text = "\n".join(details)
            
            # 2. Complete Raw MTProto JSON Payload
            from utils import safe_serialize
            raw_json = safe_serialize(message)
            
            return (
                f"{summary_text}\n\n"
                f"=== Raw MTProto API Payload (JSON) ===\n"
                f"{raw_json}"
            )
        except Exception as e:
            return f"Error retrieving message details: {str(e)}"
ROOT_TOOL_CATEGORIES = {
    "save_file_to_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "save_file_from_telegram": "Category 1: File System and Sandbox (Workspace File Management)",
    "read_file_from_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "list_workspace_files": "Category 1: File System and Sandbox (Workspace File Management)",
    "delete_file_from_workspace": "Category 1: File System and Sandbox (Workspace File Management)",
    "download_content_from_url": "Category 1: File System and Sandbox (Workspace File Management)",
    
    "internet_search": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "internet_media_search": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    "scrape_url": "Category 2: Web Search and Data Scraping (Web Search & Data Scraping)",
    
    "send_agent_message": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "execute_telegram_action": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "click_inline_button": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_inline_bot_result": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "set_message_reaction": "Category 3: Telegram Automation (Telegram Automation Actions)",
    "send_telegram_media": "Category 3: Telegram Automation (Telegram Automation Actions)",
    
    "set_task_timer": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    "delete_task_timer": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    "list_task_timers": "Category 4: Timers and Scheduler (SQLite Schedulers)",
    
    "set_wake_trigger": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    "delete_wake_trigger": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    "list_task_triggers": "Category 5: Triggers and Auto-Wake (Wake Triggers)",
    
    "generate_image": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "generate_audio": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "generate_video": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    "upload_file_to_public_host": "Category 6: Multimedia and Generative AI (Generative Multimedia AI)",
    
    "no_op_ignore": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "run_sandboxed_command": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "execute_python_code": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "upload_file_to_google": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_chat_history_from_db": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_telegram_object_info": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "get_telegram_message_details": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "execute_sql_query": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    
    "create_or_update_custom_tool": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)",
    "delete_custom_tool": "Category 7: System Control and Integration (System Control, DB & Sandboxed VM)"
}

# The sole global instance of the toolkit
toolkit = AIToolKit()


def register_system_tools():
    """Automatically registers all root methods of the AIToolKit class in the global FunctionRegistry."""
    from registry import registry
    for method_name, category in ROOT_TOOL_CATEGORIES.items():
        func = getattr(toolkit, method_name, None)
        if func:
            registry.register(
                name=method_name,
                callable_func=func,
                category=category,
                description=getattr(func, "__doc__", ""),
                is_custom=False
            )
    logger.info(f"Automatic registration completed. Successfully imported system tools: {len(ROOT_TOOL_CATEGORIES)}")


# Dynamically export all class methods to the global module namespace
# to maintain full backward compatibility with old imports in all external files
for attr_name in dir(toolkit):
    if not attr_name.startswith("_"):
        globals()[attr_name] = getattr(toolkit, attr_name)
