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

    async def internet_media_search(self, query: str, media_type: str = "image", timeout: float = WEB_MEDIA_SEARCH_TIMEOUT, auto_download: bool = None, auto_upload_google: bool = None, max_results: int = None, **kwargs) -> str:
        """
        Performs a search for multimedia files, videos, gifs, audio, or any document/file format on the Internet via DuckDuckGo.
        
        Args:
            query: Search keywords or terms.
            media_type: Category of file to locate ('image', 'gif', 'video', 'audio', 'document').
            timeout: Max network timeout in seconds.
            auto_download: Downloads matches to local sandbox if set to True.
            auto_upload_google: Uploads cached workspace files to Google File API if set to True.
            max_results: Max files to download/upload (defaults to system settings). You can alter this parameter dynamically based on the complexity or amount of results requested by the user.
        """
        if auto_download is None:
            auto_download = getattr(config, "MEDIA_SEARCH_AUTO_DOWNLOAD", True)
        if auto_upload_google is None:
            auto_upload_google = getattr(config, "MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE", True)
        if max_results is None:
            max_results = getattr(config, "MEDIA_SEARCH_MAX_RESULTS", 3)

        headers = {"User-Agent": USER_AGENT}
        search_query = query
        m_type_lower = media_type.lower().strip()

        from proxy_manager import proxy_rotator
        proxy_url = proxy_rotator.get_proxy("scraper")

        logger.info(f"Initiating media search: Query='{query}' | Media Type='{media_type}'")
        
        results = []
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True) as client_httpx:
                if m_type_lower in ["image", "gif"]:
                    # Query direct DuckDuckGo Image JSON API
                    main_url = f"https://duckduckgo.com/?q={urllib.parse.quote(search_query)}"
                    main_resp = await client_httpx.get(main_url, headers=headers)
                    vqd_match = re.search(r'vqd=([\d-]+)', main_resp.text) or re.search(r'vqd\s*=\s*[\'"]([^\'"]+)[\'"]', main_resp.text)
                    if vqd_match:
                        vqd = vqd_match.group(1)
                        api_url = f"https://duckduckgo.com/i.js"
                        params = {"q": search_query, "o": "json", "vqd": vqd, "f": ",,,", "p": "1"}
                        headers_api = headers.copy()
                        headers_api["Referer"] = "https://duckduckgo.com/"
                        api_resp = await client_httpx.get(api_url, params=params, headers=headers_api)
                        if api_resp.status_code == 200:
                            data = api_resp.json()
                            for item in data.get("results", [])[:WEB_SEARCH_RESULTS_LIMIT]:
                                img_url = item.get("image")
                                if img_url:
                                    results.append(img_url)
                
                # Fallback to standard HTML search for non-image types
                if not results:
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
                        search_query += f" filetype:{m_type_lower}"

                    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
                    resp = await client_httpx.get(url, headers=headers)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        for link in soup.find_all("a", class_="result__url")[:WEB_SEARCH_RESULTS_LIMIT]:
                            href = link.get("href", "")
                            if "uddg=" in href:
                                actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                                results.append(actual_url)
                
                if not results:
                    logger.warning(f"No results found for query: '{search_query}'")
                    return "Multimedia not found."
                output_msg = f"Search Results for '{query}' ({media_type}):\n" + "\n".join(f"- {url}" for url in results)
                
                if auto_download:
                    import time
                    from PIL import Image
                    from utils import sanitize_filename
                    from tools import download_content_from_url, upload_file_to_google
                    
                    downloaded_files = []
                    
                    # 5-turn self-healing verification loop
                    for idx, candidate_url in enumerate(results[:5]):
                        if len(downloaded_files) >= max_results:
                            break
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
                                def is_valid_img(img_url):
                                    u_low = img_url.lower()
                                    return any(ext in u_low for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]) and not any(skip in u_low for skip in ["avatar", "icon", "logo", "spinner", "badge", "default", "placeholder", "default_open_graph", "header", "footer", "button", "sprite"])

                                logger.info(f"Candidate #{idx+1} is HTML. Scraping embedded image tags and metadata...")
                                async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, headers=headers) as client_get:
                                    page_resp = await client_get.get(candidate_url)
                                    if page_resp.status_code == 200:
                                        page_soup = BeautifulSoup(page_resp.text, "html.parser")
                                        
                                        # A. Extract OpenGraph and Twitter images (highly reliable preview banners!)
                                        og_meta = page_soup.find("meta", property=re.compile(r"og:image", re.I)) or page_soup.find("meta", attrs={"name": re.compile(r"og:image", re.I)})
                                        if og_meta and og_meta.get("content"):
                                            og_url = urllib.parse.join(candidate_url, og_meta["content"]) if hasattr(urllib.parse, 'join') else urllib.parse.urljoin(candidate_url, og_meta["content"])
                                            if is_valid_img(og_url):
                                                scraped_image_urls.append(og_url)
                                            
                                        tw_meta = page_soup.find("meta", attrs={"name": re.compile(r"twitter:image", re.I)}) or page_soup.find("meta", property=re.compile(r"twitter:image", re.I))
                                        if tw_meta and tw_meta.get("content"):
                                            tw_url = urllib.parse.join(candidate_url, tw_meta["content"]) if hasattr(urllib.parse, 'join') else urllib.parse.urljoin(candidate_url, tw_meta["content"])
                                            if is_valid_img(tw_url):
                                                scraped_image_urls.append(tw_url)
                                            
                                        # B. Extract inline img tags
                                        for img_tag in page_soup.find_all("img"):
                                            img_src = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("srcset")
                                            if img_src:
                                                if "," in img_src:
                                                    img_src = img_src.split(",")[0].strip().split(" ")[0]
                                                full_img_url = urllib.parse.join(candidate_url, img_src) if hasattr(urllib.parse, 'join') else urllib.parse.urljoin(candidate_url, img_src)
                                                if is_valid_img(full_img_url):
                                                    scraped_image_urls.append(full_img_url)
                            except Exception as scrape_err:
                                logger.warning(f"Error scraping images from HTML page {candidate_url}: {str(scrape_err)}")
                        else:
                            scraped_image_urls.append(candidate_url)
                        
                        logger.info(f"Candidate #{idx+1} yielded {len(scraped_image_urls)} prospective media target URLs.")
                        
                        # Score and sort image URLs dynamically to deprioritize covers and icons without hard-blocking them
                        def score_url(cand_url: str) -> int:
                            score = 0
                            cand_lower = cand_url.lower()
                            for word in query.lower().split():
                                if word in cand_lower:
                                    score += 15
                            if any(x in cand_lower for x in ["cover", "preview", "thumbnail", "default_open_graph", "og_image"]):
                                score -= 30
                            if any(x in cand_lower for x in ["avatar", "icon", "logo", "spinner", "badge", "banner"]):
                                score -= 50
                            return score

                        scraped_image_urls.sort(key=score_url, reverse=True)
                        
                        # 3. Iterate through extracted image URLs and try to download/validate
                        for img_idx, target_url in enumerate(scraped_image_urls[:8]):
                            if len(downloaded_files) >= max_results:
                                break
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
                                            downloaded_files.append(candidate_filename)
                                            logger.info(f"Successfully downloaded and verified image candidate: {target_url}")
                                            break # Break to move to next candidate site for variety!
                                        except Exception as img_err:
                                            logger.warning(f"Candidate image failed validation: {str(img_err)}")
                                            try: candidate_path.unlink()
                                            except Exception: pass
                                    else:
                                        if candidate_path.stat().st_size > 1024:
                                            download_success = True
                                            valid_filename = candidate_filename
                                            download_success = True
                                            valid_filename = candidate_filename
                                            logger.info(f"Successfully downloaded and verified file candidate: {target_url}")
                                            break
                                        else:
                                            try: candidate_path.unlink()
                                            except Exception: pass
                            except Exception as dl_err:
                                logger.warning(f"Failed to process media candidate {target_url}: {str(dl_err)}")
                    
                    if downloaded_files:
                        output_msg += f"\n\n[Auto-Download]: Successfully downloaded {len(downloaded_files)} verified results to workspace: {', '.join(downloaded_files)}."
                        if auto_upload_google:
                            for f_idx, f_name in enumerate(downloaded_files):
                                up_res = await upload_file_to_google(f_name)
                                if isinstance(up_res, dict) and up_res.get("status") == "success":
                                    output_msg += f"\n[Auto-Upload]: Successfully uploaded candidate #{f_idx+1} '{f_name}' to Google File API. URI: {up_res.get('google_uri')} (Mime-type: {up_res.get('mime_type')}). You can view this file in the history!"
                                else:
                                    output_msg += f"\n[Auto-Upload]: Failed to upload '{f_name}' to Google File API."
                    else:
                        output_msg += f"\n\n[Auto-Download]: All search results failed to deliver valid files."
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