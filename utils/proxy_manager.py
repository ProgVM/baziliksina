# utils/proxy_manager.py
import os
import logging
import urllib.parse
import socks
import socket
from typing import List, Optional, Dict, Any

logger = logging.getLogger("ProxyManager")


class ProxyRotationManager:
    """Manages separate proxy lists and dynamic rotation for different services."""
    def __init__(self):
        self.categories: Dict[str, List[str]] = {
            "gemini": self._load_proxies("GEMINI_PROXIES"),
            "telegram": self._load_proxies("TELEGRAM_PROXIES"),
            "pollinations": self._load_proxies("POLLINATIONS_PROXIES"),
            "scraper": self._load_proxies("SCRAPER_PROXIES"),
        }
        self.indexes: Dict[str, int] = {k: 0 for k in self.categories.keys()}
        self.test_timeout = float(os.getenv("PROXY_TEST_TIMEOUT", "3.0"))
        self.strict_check = os.getenv("PROXY_STRICT_CHECK", "false").strip().lower() in ["true", "1", "yes"]

    def _load_proxies(self, env_name: str) -> List[str]:
        """Loads and cleans proxy URLs from environment variables."""
        raw = os.getenv(env_name, "")
        if not raw:
            global_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy", "")
            return [global_proxy.strip()] if global_proxy.strip() else []
        return [p.strip() for p in raw.split(",") if p.strip()]

    def check_socket_active(self, proxy_url_str: str) -> bool:
        """Performs a quick TCP connection test to verify if the proxy is reachable."""
        if not proxy_url_str:
            return False
        try:
            parsed = urllib.parse.urlparse(proxy_url_str)
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                return False
            with socket.create_connection((host, port), timeout=self.test_timeout):
                return True
        except Exception as e:
            logger.debug(f"Socket connection check failed for {proxy_url_str}: {str(e)}")
            return False

    def get_proxy(self, category: str) -> Optional[str]:
        """Returns the active proxy for the specified category."""
        proxies = self.categories.get(category, [])
        if not proxies:
            return None

        start_idx = self.indexes[category]
        for i in range(len(proxies)):
            idx = (start_idx + i) % len(proxies)
            proxy = proxies[idx]
            if self.check_socket_active(proxy):
                self.indexes[category] = idx
                return proxy
            else:
                if self.strict_check:
                    logger.warning(f"Proxy '{proxy}' in category '{category}' is unreachable. Rotating...")
                else:
                    # If strict check is disabled, trust configured proxy
                    self.indexes[category] = idx
                    return proxy

        return proxies[0] if not self.strict_check else None

    def get_telethon_proxy(self) -> Optional[Dict[str, Any]]:
        """Translates the active telegram proxy URL into universal format for Telethon."""
        proxy_url = self.get_proxy("telegram")
        if not proxy_url:
            return None
        try:
            parsed = urllib.parse.urlparse(proxy_url)
            scheme = (parsed.scheme or "socks5").lower()
            
            p_type = socks.SOCKS5 if "socks5" in scheme else socks.SOCKS4 if "socks4" in scheme else socks.HTTP
            p_host = parsed.hostname or "127.0.0.1"
            p_port = parsed.port or 10808
            
            # Universal Telethon dict compatible with both PySocks and python-socks
            proxy_dict = {
                'proxy_type': 'socks5' if 'socks5' in scheme else ('socks4' if 'socks4' in scheme else 'http'),
                'addr': p_host,
                'port': int(p_port),
                'rdns': True
            }
            if parsed.username:
                proxy_dict['username'] = parsed.username
            if parsed.password:
                proxy_dict['password'] = parsed.password

            logger.info(f"Telethon configured with proxy: {scheme}://{p_host}:{p_port}")
            return proxy_dict
        except Exception as e:
            logger.error(f"Failed to parse Telethon proxy: {str(e)}")
        return None


# Global singleton instance
proxy_rotator = ProxyRotationManager()
