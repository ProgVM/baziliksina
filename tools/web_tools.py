# tools/web_tools.py
import os
import re
import json
import logging
from typing import Any
import urllib.parse
from bs4 import BeautifulSoup
import httpx

import config
from config import USER_AGENT, WEB_SEARCH_TIMEOUT, WEB_MEDIA_SEARCH_TIMEOUT, SCRAPE_TIMEOUT, WEB_SEARCH_RESULTS_LIMIT, SCRAPE_CHAR_LIMIT
import tools

logger = logging.getLogger("Tools.Web")

class AIToolKitWeb:
    async def internet_search(self, query: str, timeout: float = WEB_SEARCH_TIMEOUT, **kwargs) -> str:
        """Performs a text search on the Internet for a given query via DuckDuckGo and returns brief results."""
        headers = {"User-Agent": USER_AGENT}
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

    async def internet_media_search(self, query: str, media_type: str = "image", timeout: float = WEB_MEDIA_SEARCH_TIMEOUT, auto_download: bool = None, auto_upload_google: bool = None, **kwargs) -> str:
        """
        Performs a search for multimedia files, videos, gifs, audio, or any document/file format on the Internet via DuckDuckGo.
        If auto_download is True, automatically downloads the first result to the workspace.
        If auto_upload_google is True, also uploads the downloaded media to Google File API so the AI can see it.
        """
        if auto_download is None:
            auto_download = getattr(config, "MEDIA_SEARCH_AUTO_DOWNLOAD", True)
        if auto_upload_google is None:
            auto_upload_google = getattr(config, "MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE", True)

        headers = {"User-Agent": USER_AGENT}
        search_query = query
        
        # Tailor the DuckDuckGo query dynamically based on the requested media type
        m_type_lower = media_type.lower().strip()
        if m_type_lower == "document":
            search_query += " filetype:pdf"
        elif m_type_lower == "image":
            search_query += " format:jpg"
        elif m_type_lower == "video":
            search_query += " filetype:mp4"
        elif m_type_lower == "gif":
            search_query += " filetype:gif"
        elif m_type_lower == "audio":
            search_query += " filetype:mp3"
        elif m_type_lower.isalnum():
            # Support any arbitrary extension, e.g. "zip", "xlsx", "epub"
            search_query += f" filetype:{m_type_lower}"

        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")
        
        logger.info(f"Initiating media search: Query='{query}' | Media Type='{media_type}' | URL={url}")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.error(f"DuckDuckGo search failed with HTTP status code {resp.status_code}")
                    return f"Media search failed, code: {resp.status_code}"
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                
                # Parse the search result URLs
                for link in soup.find_all("a", class_="result__url")[:WEB_SEARCH_RESULTS_LIMIT]:
                    href = link.get("href", "")
                    if "uddg=" in href:
                        actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                        results.append(actual_url)
                
                if not results:
                    logger.warning(f"No results found for query: '{search_query}'")
                    return "Multimedia not found."
                
                logger.info(f"Found {len(results)} search result URLs. Proceeding to candidate extraction.")
                output_msg = f"Search Results for '{query}' ({media_type}):\n" + "\n".join(f"- {url}" for url in results)
                
                if auto_download:
                    import time
                    from PIL import Image
                    from utils import sanitize_filename
                    from tools import download_content_from_url, upload_file_to_google
                    
                    download_success = False
                    valid_filename = None
                    
                    # 5-turn self-healing verification loop
                    for idx, candidate_url in enumerate(results[:5]):
                        # 1. Pre-check Content-Type via a lightweight HEAD or GET request
                        is_html = False
                        content_type = ""
                        try:
                            logger.info(f"Checking candidate #{idx+1} content type: {candidate_url}")
                            async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client_head:
                                head_resp = await client_head.head(candidate_url)
                                if head_resp.status_code == 200:
                                    content_type = head_resp.headers.get("Content-Type", "").lower()
                                else:
                                    # Fallback to GET if HEAD method is not supported
                                    head_resp = await client_get.get(candidate_url)
                                    content_type = head_resp.headers.get("Content-Type", "").lower()
                        except Exception as head_err:
                            logger.debug(f"Pre-check failed for candidate #{idx+1} ({candidate_url}): {str(head_err)}")
                        
                        if "text/html" in content_type or "application/xhtml" in content_type:
                            is_html = True
                        
                        # 2. Extract potential image candidates from HTML or treat direct URL as candidate
                        scraped_image_urls = []
                        if is_html:
                            try:
                                logger.info(f"Candidate #{idx+1} is HTML. Scraping embedded image tags and metadata...")
                                async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, headers=headers) as client_get:
                                    page_resp = await client_get.get(candidate_url)
                                    if page_resp.status_code == 200:
                                        page_soup = BeautifulSoup(page_resp.text, "html.parser")
                                        
                                        # A. Extract OpenGraph and Twitter images (highly reliable preview banners!)
                                        og_meta = page_soup.find("meta", property=re.compile(r"og:image", re.I)) or page_soup.find("meta", name=re.compile(r"og:image", re.I))
                                        if og_meta and og_meta.get("content"):
                                            scraped_image_urls.append(urllib.parse.urljoin(candidate_url, og_meta["content"]))
                                            
                                        tw_meta = page_soup.find("meta", name=re.compile(r"twitter:image", re.I)) or page_soup.find("meta", property=re.compile(r"twitter:image", re.I))
                                        if tw_meta and tw_meta.get("content"):
                                            scraped_image_urls.append(urllib.parse.urljoin(candidate_url, tw_meta["content"]))
                                            
                                        # B. Extract inline img tags
                                        for img_tag in page_soup.find_all("img"):
                                            img_src = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("srcset")
                                            if img_src:
                                                if "," in img_src:
                                                    img_src = img_src.split(",")[0].strip().split(" ")[0]
                                                full_img_url = urllib.parse.urljoin(candidate_url, img_src)
                                                full_img_url = full_img_url.split("?")[0]
                                                
                                                # Simple path/name heuristics to filter out low-res junk, logos, and icons
                                                if any(ext in full_img_url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                                                    if not any(skip in full_img_url.lower() for skip in ["avatar", "icon", "logo", "spinner", "badge"]):
                                                        scraped_image_urls.append(full_img_url)
                            except Exception as scrape_err:
                                logger.warning(f"Error scraping images from HTML page {candidate_url}: {str(scrape_err)}")
                        else:
                            # Direct media file link
                            scraped_image_urls.append(candidate_url)
                        
                        logger.info(f"Candidate #{idx+1} yielded {len(scraped_image_urls)} prospective media target URLs.")
                        
                        # 3. Iterate through extracted image URLs and try to download/validate
                        for img_idx, target_url in enumerate(scraped_image_urls[:8]):
                            try:
                                parsed_path = urllib.parse.urlparse(target_url).path
                                url_ext = os.path.splitext(parsed_path)[1].lower()
                                if not url_ext or len(url_ext) > 5 or url_ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                                    url_ext = ".jpg"
                                    
                                candidate_filename = f"search_{sanitize_filename(query)}_{idx}_{img_idx}_{int(time.time())}{url_ext}"
                                candidate_path = config.WORKSPACE_DIR / candidate_filename
                                
                                logger.info(f"Attempting download for prospective target #{img_idx+1}: {target_url}")
                                dl_res = await download_content_from_url(target_url, filename=candidate_filename, timeout=timeout)
                                
                                if "Success" in dl_res and candidate_path.exists():
                                    if m_type_lower in ["image", "gif"]:
                                        try:
                                            with Image.open(candidate_path) as img:
                                                img.verify()
                                            download_success = True
                                            valid_filename = candidate_filename
                                            logger.info(f"Successfully downloaded and verified image candidate: {target_url}")
                                            break
                                        except Exception as img_err:
                                            logger.warning(f"Candidate image failed validation: {str(img_err)}")
                                            try: candidate_path.unlink()
                                            except Exception: pass
                                    else:
                                        if candidate_path.stat().st_size > 1024:
                                            download_success = True
                                            valid_filename = candidate_filename
                                            logger.info(f"Successfully downloaded and verified file candidate: {target_url}")
                                            break
                                        else:
                                            try: candidate_path.unlink()
                                            except Exception: pass
                            except Exception as dl_err:
                                logger.warning(f"Failed to process media candidate {target_url}: {str(dl_err)}")
                        
                        if download_success:
                            break
                    
                    if download_success and valid_filename:
                        output_msg += f"\n\n[Auto-Download]: Successfully downloaded verified result to workspace as '{valid_filename}'."
                        if auto_upload_google:
                            up_res = await upload_file_to_google(valid_filename)
                            if isinstance(up_res, dict) and up_res.get("status") == "success":
                                output_msg += f"\n[Auto-Upload]: Successfully uploaded to Google File API. URI: {up_res.get('google_uri')} (Mime-type: {up_res.get('mime_type')}). You can view this file in the history!"
                            else:
                                output_msg += f"\n[Auto-Upload]: Failed to upload to Google File API: {up_res.get('message') if isinstance(up_res, dict) else str(up_res)}"
                    else:
                        output_msg += f"\n\n[Auto-Download]: All search results failed to deliver a valid, non-corrupted media file."
                return output_msg
        except Exception as e:
            logger.error(f"Error searching for media: {str(e)}")
            return f"Error searching for media: {str(e)}"

    async def scrape_url(self, url: str, timeout: float = SCRAPE_TIMEOUT, **kwargs) -> str:
        """Extracts clean text content of a web page at the specified URL."""
        headers = {"User-Agent": USER_AGENT}
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

    async def send_http_request(self, method: str, url: str, headers_json: str = None, params_json: str = None, data_json: str = None, timeout: float = 30.0, **kwargs) -> str:
        """Sends an HTTP/HTTPS request (GET, POST, PUT, DELETE, PATCH, etc.) to any external resource."""
        headers = json.loads(headers_json) if headers_json else {}
        params = json.loads(params_json) if params_json else {}
        data = json.loads(data_json) if data_json else None
        
        if kwargs:
            if isinstance(data, dict):
                data.update(kwargs)
            elif data is None:
                data = kwargs
                
        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True) as client_httpx:
                resp = await client_httpx.request(method=method.upper(), url=url, headers=headers, params=params, json=data)
                return f"HTTP Response (Status: {resp.status_code}):\n{resp.text[:4000]}"
        except Exception as e:
            return f"Error sending HTTP request: {str(e)}"

# Export methods to module level
toolkit_web = AIToolKitWeb()
for attr in dir(toolkit_web):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_web, attr)
