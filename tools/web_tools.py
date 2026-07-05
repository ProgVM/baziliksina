# tools/web_tools.py
import os
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
        Performs a search for multimedia files or PDF documents on the Internet via DuckDuckGo.
        If auto_download is True, automatically downloads the first result to the workspace.
        If auto_upload_google is True, also uploads the downloaded media to Google File API so the AI can see it.
        """
        if auto_download is None:
            auto_download = getattr(config, "MEDIA_SEARCH_AUTO_DOWNLOAD", True)
        if auto_upload_google is None:
            auto_upload_google = getattr(config, "MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE", True)

        headers = {"User-Agent": USER_AGENT}
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
                
                if not results:
                    return "Multimedia not found."
                
                output_msg = f"Search Results for '{query}':\n" + "\n".join(f"- {url}" for url in results)
                
                if auto_download and media_type in ["image", "document"]:
                    import time
                    from utils import sanitize_filename
                    ext = ".jpg" if media_type == "image" else ".pdf"
                    filename = f"search_{sanitize_filename(query)}_{int(time.time())}{ext}"
                    
                    from tools import download_content_from_url
                    dl_res = await download_content_from_url(results[0], filename=filename, timeout=timeout)
                    
                    if "Success" in dl_res:
                        output_msg += f"\n\n[Auto-Download]: Successfully downloaded top result to workspace as '{filename}'."
                        
                        if auto_upload_google:
                            from tools import upload_file_to_google
                            up_res = await upload_file_to_google(filename)
                            if isinstance(up_res, dict) and up_res.get("status") == "success":
                                output_msg += f"\n[Auto-Upload]: Successfully uploaded to Google File API. URI: {up_res.get('google_uri')} (Mime-type: {up_res.get('mime_type')}). You can view this file in the history!"
                            else:
                                output_msg += f"\n[Auto-Upload]: Failed to upload to Google File API: {up_res.get('message') if isinstance(up_res, dict) else str(up_res)}"
                return output_msg
        except Exception as e:
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
