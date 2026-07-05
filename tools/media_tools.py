# tools/media_tools.py
import os
import json
import logging
import random
import urllib.parse
import httpx
from PIL import Image

import config
from config import (
    WORKSPACE_DIR, DEFAULT_IMAGE_MODEL, DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT,
    GENERATE_IMAGE_TIMEOUT, DEFAULT_AUDIO_VOICE, DEFAULT_AUDIO_MODEL,
    GENERATE_AUDIO_TIMEOUT, DEFAULT_VIDEO_MODEL, DEFAULT_VIDEO_DURATION,
    DEFAULT_VIDEO_ASPECT_RATIO, GENERATE_VIDEO_TIMEOUT, DEFAULT_IMAGE_NAME,
    DEFAULT_AUDIO_NAME, DEFAULT_VIDEO_NAME, DEFAULT_PUBLIC_UPLOAD_PROVIDER,
    PUBLIC_UPLOAD_TIMEOUT, USER_AGENT
)
import tools

logger = logging.getLogger("Tools.Media")

class AIToolKitMedia:
    async def generate_image(self, prompt: str, model: str = DEFAULT_IMAGE_MODEL, width: int = DEFAULT_IMAGE_WIDTH, height: int = DEFAULT_IMAGE_HEIGHT, seed: int = -1, reference_image_url: str = None, timeout: float = GENERATE_IMAGE_TIMEOUT, **kwargs) -> str:
        """Generates high-quality images from a text description on Pollinations.ai."""
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
            params["seed"] = random.randint(config.POLLINATIONS_SEED_MIN, config.POLLINATIONS_SEED_MAX)
        if "nanobanana" in model:params["reasoning"] = "pro"
        if reference_image_url:
            params["referenceImage"] = reference_image_url
        if kwargs:
            params.update(kwargs)

        try:
            resp = await tools.call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Pollinations AI error: status {resp.status_code}"
            image_bytes = resp.content
            out_filename = DEFAULT_IMAGE_NAME
            out_path = WORKSPACE_DIR / out_filename
            with open(out_path, "wb") as f:
                f.write(image_bytes)
            cid = tools.current_chat_id.get()
            return (
                f"Image generated and saved as '{out_filename}'.\n"
                f"To send the file, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"caption\": \"Your prompt\"}}')"
            )
        except Exception as e:
            return f"Image generation error: {str(e)}"

    async def generate_audio(self, prompt: str, voice: str = DEFAULT_AUDIO_VOICE, model: str = DEFAULT_AUDIO_MODEL, timeout: float = GENERATE_AUDIO_TIMEOUT, **kwargs) -> str:
        """Synthesizes high-quality speech or music from a text description on Pollinations.ai."""
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
            resp = await tools.call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Audio generation error: status {resp.status_code}"
            audio_bytes = resp.content
            out_filename = DEFAULT_AUDIO_NAME
            with open(WORKSPACE_DIR / out_filename, "wb") as f:
                f.write(audio_bytes)
            cid = tools.current_chat_id.get()
            return (
                f"Speech synthesized and saved as '{out_filename}'.\n"
                f"To send, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"voice\": true}}')"
            )
        except Exception as e:
            return f"Audio synthesis error: {str(e)}"

    async def generate_video(self, prompt: str, model: str = DEFAULT_VIDEO_MODEL, duration: int = DEFAULT_VIDEO_DURATION, aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO, seed: int = -1, timeout: float = GENERATE_VIDEO_TIMEOUT, **kwargs) -> str:
        """Generates a short video animation from a text description on Pollinations.ai."""
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
            params["seed"] = random.randint(config.POLLINATIONS_SEED_MIN, config.POLLINATIONS_SEED_MAX)
        if kwargs:params.update(kwargs)
        try:
            logger.info("Launching video generation...")
            resp = await tools.call_pollinations_api(url, params, timeout=timeout)
            if resp.status_code != 200:
                return f"Video generation error: status {resp.status_code}"
            video_bytes = resp.content
            out_filename = DEFAULT_VIDEO_NAME
            with open(WORKSPACE_DIR / out_filename, "wb") as f:
                f.write(video_bytes)
            cid = tools.current_chat_id.get()
            return (
                f"Video clip generated and saved as '{out_filename}'.\n"
                f"To send, call the function:\n"
                f"execute_telegram_action(method_name='send_file', args_json='{{\"entity\": {cid}, \"file\": \"{out_filename}\", \"caption\": \"Your video\"}}')"
            )
        except Exception as e:
            return f"Video generation error: {str(e)}"

    async def upload_file_to_public_host(self, filename: str, provider: str = DEFAULT_PUBLIC_UPLOAD_PROVIDER, timeout: float = PUBLIC_UPLOAD_TIMEOUT, **kwargs) -> str:
        """Uploads a media file or document from the local AI sandbox to Telegraph, file.io, or Uguu.se."""
        import time
        if not tools.client:
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
        headers = {"User-Agent": USER_AGENT}
        temp_jpg_path = None
        try:
            if ext == "png" and provider in ["telegraph", "auto"]:
                try:
                    logger.info("PNG detected. Converting to JPEG to bypass transparency limits...")
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        background = Image.new("RGB", img.size, (255, 255, 255))
                        mask = img.split()[3] if img.mode == "RGBA" else img.split()[1] if img.mode == "LA" else None
                        background.paste(img, mask=mask)
                        img = background
                    else:
                        img = img.convert("RGB")
                    temp_jpg_path = file_path.with_name(f"temp_upload_{int(time.time())}.jpg")
                    img.save(temp_jpg_path, "JPEG", quality=config.POLLINATIONS_UPLOAD_JPEG_QUALITY)
                    file_path = temp_jpg_path
                    ext = "jpg"
                    filename = temp_jpg_path.name
                except Exception as conv_err:
                    logger.warning(f"Failed to convert PNG: {str(conv_err)}")

            if provider in ["pollinations", "auto"]:
                try:
                    current_key = await tools.pollinations_key_manager.get_active_key() if tools.pollinations_key_manager else ""
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
                                    return f"File '{filename}' successfully uploaded to PollinationsAI!\nPublic URL: {public_url}"
                except Exception as e:
                    logger.warning(f"Upload to PollinationsAI failed: {str(e)}")
                    if provider == "pollinations":
                        return f"Error: {str(e)}"

            if provider in ["telegraph", "auto"] and ext in supported_exts:
                url = "https://telegra.ph/upload"
                try:
                    with open(file_path, "rb") as f:
                        files = {"file": ("file", f, supported_exts[ext])}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                            resp = await client_httpx.post(url, files=files, headers=headers)
                            if resp.status_code == 200:
                                data = resp.json()
                                if isinstance(data, list) and len(data) > 0 and "src" in data[0]:
                                    return f"File '{filename}' successfully uploaded to Telegraph!\nPublic URL: https://telegra.ph{data[0]['src']}"
                except Exception as e:
                    logger.warning(f"Telegraph upload failed: {str(e)}")

            if provider in ["file.io", "auto"]:
                try:
                    with open(file_path, "rb") as f:
                        files = {"file": (os.path.basename(file_path), f)}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                            resp = await client_httpx.post("https://file.io/", files=files, headers=headers)
                            if resp.status_code == 200 and resp.json().get("success"):
                                return f"File '{filename}' successfully uploaded to file.io!\nPublic URL: {resp.json().get('link')}"
                except Exception as e:
                    logger.warning(f"file.io upload failed: {str(e)}")

            if provider in ["uguu.se", "auto"]:
                try:
                    with open(file_path, "rb") as f:
                        mime_val = "image/jpeg" if ext in ["jpg", "jpeg"] else "image/png" if ext == "png" else "application/octet-stream"
                        files = {"files[]": (os.path.basename(file_path), f, mime_val)}
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client_httpx:
                            resp = await client_httpx.post("https://uguu.se/upload", files=files, headers=headers)
                            if resp.status_code == 200 and resp.json().get("success"):
                                return f"File '{filename}' successfully uploaded to Uguu.se!\nPublic URL: {resp.json()['files'][0]['url']}"
                except Exception as e:
                    logger.error(f"Uguu.se upload failed: {str(e)}")

            return "Error: All hosting providers failed."
        finally:
            if temp_jpg_path and temp_jpg_path.exists():
                try: temp_jpg_path.unlink()
                except Exception: pass

# Export methods to module level
toolkit_media = AIToolKitMedia()
for attr in dir(toolkit_media):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_media, attr)
