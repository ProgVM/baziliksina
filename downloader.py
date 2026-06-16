# downloader.py
import os
import json
import logging
import asyncio
from pathlib import Path
from PIL import Image
from config import WORKSPACE_DIR, MAX_FILE_SIZE, AVATAR_CACHE_TIME, FFMPEG_PATH, CONVERSION_TIMEOUT

logger = logging.getLogger("Downloader")

# Cache directories
EMOJI_CACHE_DIR = WORKSPACE_DIR / "emoji_cache"
AVATAR_CACHE_DIR = WORKSPACE_DIR / "avatar_cache"
GIFT_CACHE_DIR = WORKSPACE_DIR / "gift_cache"
TEMP_MEDIA_DIR = WORKSPACE_DIR / "temp_media"

# Create directories during module initialization
for directory in [EMOJI_CACHE_DIR, AVATAR_CACHE_DIR, GIFT_CACHE_DIR, TEMP_MEDIA_DIR]:
    directory.mkdir(exist_ok=True)


async def convert_webm_to_mp4(webm_path: str) -> str:
    """
    Converts animated .webm sticker or emoji (VP9 with transparency) to standard .mp4 (H.264)
    via ffmpeg for full compatibility with Google Gemini API.
    """
    path = Path(webm_path)
    out_path = path.with_suffix(".mp4")
    
    try:
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", str(path),
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-an",  # disable audio since stickers and emojis do not have it
            str(out_path)
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CONVERSION_TIMEOUT)
        
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            logger.info(f"WebM successfully converted to MP4: {out_path.name}")
            try:
                path.unlink() # Delete the original .webm from disk
            except Exception:
                pass
            return str(out_path.resolve())
        else:
            logger.warning(f"Failed to convert WebM via FFmpeg. Code: {proc.returncode}")
    except FileNotFoundError:
        logger.warning(
            f"Utility '{FFMPEG_PATH}' not found in the system. "
            "It is recommended to install it (e.g. 'pkg install ffmpeg' in Termux) so that the AI can play WebM!"
        )
    except asyncio.TimeoutError:
        logger.error(f"Timeout exceeded {CONVERSION_TIMEOUT} sec to transcode the animated sticker.")
    except Exception as e:
        logger.error(f"Error calling ffmpeg converter: {str(e)}")
        
    return None


async def convert_ogg_to_mp3(ogg_path: str) -> str:
    """
    Converts .ogg (Opus) voice message to standard .mp3 (H.264)
    via ffmpeg for 100% compatibility with all versions of Google Gemini API.
    """
    path = Path(ogg_path)
    out_path = path.with_suffix(".mp3")
    
    try:
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", str(path),
            "-codec:a", "libmp3lame",
            "-qscale:a", "2",
            str(out_path)
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CONVERSION_TIMEOUT)
        
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            logger.info(f"Voice message successfully converted to MP3: {out_path.name}")
            try:
                path.unlink() # Delete the original .ogg file
            except Exception:
                pass
            return str(out_path.resolve())
        else:
            logger.warning(f"Failed to convert OGG to MP3 via FFmpeg. Code: {proc.returncode}")
    except FileNotFoundError:
        logger.warning(f"Utility '{FFMPEG_PATH}' not found. Leaving voice message in OGG format.")
    except asyncio.TimeoutError:
        logger.error(f"Timeout exceeded {CONVERSION_TIMEOUT} sec to transcode the voice message.")
    except Exception as e:
        logger.error(f"Error calling ffmpeg for OGG conversion: {str(e)}")
        
    return None


def check_and_clean_corrupted_file(file_path: str, mime_type: str) -> bool:
    """
    Performs a deep integrity check on downloaded media files.
    If the file is corrupted or has zero size, deletes it from the disk.
    """
    path = Path(file_path)
    if not path.exists() or path.stat().st_size <= 0:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        return False

    if "image" in mime_type:
        try:
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception as e:
            logger.warning(f"Image {file_path} is corrupted: {str(e)}. Self-cleaning...")
            try:
                path.unlink()
            except Exception:
                pass
            return False

    elif "video" in mime_type or "audio" in mime_type:
        if path.stat().st_size < 128:
            logger.warning(f"File {file_path} is too small ({path.stat().st_size} bytes) for media. Deleting...")
            try:
                path.unlink()
            except Exception:
                pass
            return False

    return True


async def get_cached_premium_emoji(client, document_id: int, is_animated: bool = False) -> str:
    """
    Downloads and caches premium custom emojis (static .webp or animated .webm/.mp4).
    Animated emojis are automatically converted to standard .mp4 (H.264) for AI compatibility.
    """
    ext = "mp4" if is_animated else "webp"
    filename = f"emoji_{document_id}.{ext}"
    local_path = EMOJI_CACHE_DIR / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        return str(local_path.resolve())

    try:
        from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
        res = await client(GetCustomEmojiDocumentsRequest(document_id=[int(document_id)]))
        if res and len(res) > 0:
            doc_obj = res[0]
            
            if is_animated:
                logger.info(f"Downloading and converting animated premium emoji ID {document_id}...")
                temp_webm_path = EMOJI_CACHE_DIR / f"temp_{document_id}.webm"
                
                # Download the original animated WebM to a temporary file
                path = await client.download_media(doc_obj, file=str(temp_webm_path))
                if path and check_and_clean_corrupted_file(path, doc_obj.mime_type):
                    # Convert it to MP4
                    mp4_path = await convert_webm_to_mp4(path)
                    if mp4_path:
                        # Rename to canonical emoji_{id}.mp4
                        Path(mp4_path).rename(local_path)
                        logger.info(f"Animated emoji {document_id} successfully saved as MP4.")
                        return str(local_path.resolve())
                    else:
                        # Fallback case: if ffmpeg is missing, save the original WebM to avoid losing the file
                        fallback_webm = EMOJI_CACHE_DIR / f"emoji_{document_id}.webm"
                        Path(path).rename(fallback_webm)
                        return str(fallback_webm.resolve())
            else:
                logger.info(f"Downloading static premium emoji ID {document_id}...")
                path = await client.download_media(doc_obj, file=str(local_path))
                if path and check_and_clean_corrupted_file(path, doc_obj.mime_type):
                    return str(local_path.resolve())
    except Exception as e:
        logger.error(f"Failed to download custom emoji {document_id}: {str(e)}")
    
    return None


async def get_cached_avatar(client, entity, is_video: bool = False) -> str:
    """
    Downloads profile avatar (photo or video avatar .mp4).
    Automatically invalidates cache when a new photo_id is detected on Telegram servers.
    """
    entity_id = getattr(entity, "id", None)
    if not entity_id:
        return None

    # Extract unique server photo_id for precise cache invalidation
    photo_id = "no_photo"
    entity_photo = getattr(entity, "photo", None)
    if entity_photo:
        photo_id = getattr(entity_photo, "photo_id", "no_photo")

    ext = "mp4" if is_video else "jpg"
    # photo_id is included in the filename for 100% accurate cleanup upon update
    filename = f"avatar_{entity_id}_{photo_id}.{ext}"
    local_path = AVATAR_CACHE_DIR / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        return str(local_path.resolve())

    # Before downloading a new avatar, clean up old cache files of the same user
    try:
        for old_file in AVATAR_CACHE_DIR.glob(f"avatar_{entity_id}_*.*"):
            old_file.unlink()
            logger.info(f"Old avatar cache {old_file.name} successfully deleted.")
    except Exception as e:
        logger.debug(f"Failed to clean up old avatar files: {str(e)}")

    try:
        logger.info(f"Downloading updated avatar for ID {entity_id} (photo_id: {photo_id})...")
        path = await client.download_profile_photo(entity, file=str(local_path), download_big=True)
        if path and check_and_clean_corrupted_file(path, "video/mp4" if is_video else "image/jpeg"):
            return str(local_path.resolve())
    except Exception as e:
        logger.debug(f"Failed to download avatar for ID {entity_id}: {str(e)}")
    
    return None


async def get_cached_gift_animation(client, gift_id: int) -> str:
    """Downloads and caches original Telegram gift animations."""
    filename = f"gift_{gift_id}.tgs"
    local_path = GIFT_CACHE_DIR / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        return str(local_path.resolve())

    try:
        from telethon.tl.types import InputDocument
        doc_input = InputDocument(id=gift_id, access_hash=0, file_reference=b'')
        path = await client.download_media(doc_input, file=str(local_path))
        if path and check_and_clean_corrupted_file(path, "application/x-tgsticker"):
            return str(local_path.resolve())
    except Exception as e:
        logger.error(f"Failed to download gift {gift_id}: {str(e)}")
    
    return None


async def download_and_cache_media(client, message, is_private: bool, mentioned: bool) -> str:
    f"""
    Universal extractor and downloader for regular attachments, voice messages, video notes, and files from polls.
    Limits the maximum size to {MAX_FILE_SIZE}. Returns JSON with local path and type.
    """
    if not (is_private or mentioned) or not message.media:
        return None

    target_media = message.media
    media_name = type(target_media).__name__

    # Extracting files from polls
    if media_name == "MessageMediaPoll":
        poll_obj = target_media.poll
        if getattr(poll_obj, "media", None):
            target_media = poll_obj.media
        elif getattr(poll_obj, "explanation_media", None):
            target_media = poll_obj.explanation_media
        else:
            for opt in getattr(poll_obj, "answers", []):
                if getattr(opt, "media", None):
                    target_media = opt.media
                    break

    file_size = 0
    mime_type = "application/octet-stream"
    t_media_name = type(target_media).__name__

    if t_media_name == "MessageMediaPhoto":
        mime_type = "image/jpeg"
        if getattr(target_media.photo, "sizes", None):
            file_size = target_media.photo.sizes[-1].size if hasattr(target_media.photo.sizes[-1], "size") else 1024 * 1024
    elif t_media_name == "MessageMediaDocument":
        doc = target_media.document
        file_size = doc.size
        mime_type = doc.mime_type or "application/octet-stream"
    else:
        return None

    if file_size > MAX_FILE_SIZE:
        logger.warning(f"Attachment {t_media_name} skipped: size exceeds the allowed limit.")
        return None

    supported_mimes = [
        "image/jpeg", "image/png", "image/webp", "image/gif",
        "audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
        "application/pdf", "text/plain", "video/mp4", "video/webm"
    ]

    if mime_type in supported_mimes or "image" in mime_type or "audio" in mime_type:
        try:
            logger.info(f"Downloading attachment ({mime_type}, size: {file_size} bytes)...")
            path = await client.download_media(target_media, file=str(TEMP_MEDIA_DIR))
            
            if path and check_and_clean_corrupted_file(path, mime_type):
                logger.info(f"Media file successfully downloaded and saved: {path}")
                
                # If animated WebM is downloaded, automatically convert it to MP4
                if "webm" in mime_type or path.endswith(".webm"):
                    mp4_path = await convert_webm_to_mp4(path)
                    if mp4_path:
                        path = mp4_path
                        mime_type = "video/mp4" # Replace type for AI
                
                # If a .ogg voice message is downloaded, automatically convert it to MP3!
                elif "ogg" in mime_type or path.endswith(".ogg"):
                    mp3_path = await convert_ogg_to_mp3(path)
                    if mp3_path:
                        path = mp3_path
                        mime_type = "audio/mp3" # Replace type with MP3 for AI
                
                return json.dumps({
                    "path": path,
                    "mime_type": mime_type
                })
        except Exception as e:
            logger.error(f"Error downloading attachment: {str(e)}")
    
    return None
