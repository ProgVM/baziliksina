# utils/proxy_manager.py
import os
import logging
import urllib.parse
import socket
from typing import List, Optional, Dict, Any, Tuple
import socks
from dotenv import load_dotenv

load_dotenv(override=True)
logger = logging.getLogger("ProxyManager")


def parse_proxy_url(proxy_url_str: str) -> Optional[Dict[str, Any]]:
    """
    Universal parser for all proxy schemes and Telegram proxy URLs:
    - tg://proxy?server=...&port=...&secret=...
    - https://t.me/proxy?server=...&port=...&secret=...
    - tg://socks?server=...&port=...&user=...&pass=...
    - https://t.me/socks?server=...&port=...&user=...&pass=...
    - mtproto://... / mtproxy://...
    - socks5://..., socks4://..., http://..., https://...
    """
    if not proxy_url_str or not isinstance(proxy_url_str, str):
        return None

    raw = proxy_url_str.strip()
    if not raw:
        return None

    try:
        # 1. Telegram URI Schemes: tg://proxy?... or tg://socks?...
        if raw.startswith(("tg://", "tgproxy://")):
            parsed = urllib.parse.urlparse(raw)
            action = (parsed.netloc or parsed.path).lower().strip("/")
            qs = urllib.parse.parse_qs(parsed.query)

            server = qs.get("server", [None])[0] or qs.get("ip", [None])[0]
            port_str = qs.get("port", ["443"])[0]
            secret = qs.get("secret", [""])[0]
            user = qs.get("user", [None])[0] or qs.get("username", [None])[0]
            passwd = qs.get("pass", [None])[0] or qs.get("password", [None])[0]

            if not server:
                return None

            port = int(port_str) if port_str.isdigit() else 443

            if action in ["socks", "socks5"]:
                return {"type": "socks5", "host": server, "port": port, "username": user, "password": passwd}
            else:
                return {"type": "mtproxy", "host": server, "port": port, "secret": secret}

        # 2. Web links: https://t.me/proxy?... or https://t.me/socks?...
        if "t.me/" in raw or "telegram.me/" in raw:
            parsed = urllib.parse.urlparse(raw)
            path_clean = parsed.path.lower().strip("/")
            qs = urllib.parse.parse_qs(parsed.query)

            server = qs.get("server", [None])[0] or qs.get("ip", [None])[0]
            port_str = qs.get("port", ["443"])[0]
            secret = qs.get("secret", [""])[0]
            user = qs.get("user", [None])[0] or qs.get("username", [None])[0]
            passwd = qs.get("pass", [None])[0] or qs.get("password", [None])[0]

            if not server:
                return None

            port = int(port_str) if port_str.isdigit() else 443

            if path_clean in ["socks", "socks5"]:
                return {"type": "socks5", "host": server, "port": port, "username": user, "password": passwd}
            else:
                return {"type": "mtproxy", "host": server, "port": port, "secret": secret}

        # 3. Direct MTProto / MTProxy schemes: mtproto://... or mtproxy://...
        if raw.startswith(("mtproto://", "mtproxy://")):
            parsed = urllib.parse.urlparse(raw)
            qs = urllib.parse.parse_qs(parsed.query)
            host = parsed.hostname
            port = parsed.port or 443
            secret = qs.get("secret", [""])[0] or parsed.username or parsed.password or parsed.path.lstrip("/")

            if not host:
                return None

            return {"type": "mtproxy", "host": host, "port": int(port), "secret": secret}

        # 4. Standard SOCKS / HTTP URIs (socks5://..., http://...)
        parsed = urllib.parse.urlparse(raw)
        scheme = (parsed.scheme or "socks5").lower()
        host = parsed.hostname
        port = parsed.port or (1080 if "socks" in scheme else (443 if "https" in scheme else 80))

        if not host:
            return None

        proxy_type = "socks5" if "socks5" in scheme else ("socks4" if "socks4" in scheme else "http")
        return {
            "type": proxy_type,
            "host": host,
            "port": int(port),
            "username": parsed.username,
            "password": parsed.password,
        }
    except Exception as e:
        logger.error(f"Error parsing proxy URL '{proxy_url_str}': {str(e)}")
        return None


class ProxyRotationManager:
    """Manages separate proxy pools and dynamic rotation across different subsystem categories."""
    def __init__(self):
        self.test_timeout = float(os.getenv("PROXY_TEST_TIMEOUT", "3.0"))
        self.strict_check = os.getenv("PROXY_STRICT_CHECK", "false").strip().lower() in ["true", "1", "yes"]
        self.categories: Dict[str, List[str]] = {}
        self.indexes: Dict[str, int] = {}
        self.reload_proxies()

    def reload_proxies(self):
        """Reloads proxy lists from environment variables and dynamic configuration."""
        self.categories = {
            "gemini": self._load_proxies("GEMINI_PROXIES"),
            "telegram": self._load_proxies("TELEGRAM_PROXIES"),
            "pollinations": self._load_proxies("POLLINATIONS_PROXIES"),
            "scraper": self._load_proxies("SCRAPER_PROXIES"),
        }
        for k in self.categories.keys():
            if k not in self.indexes:
                self.indexes[k] = 0

    def _load_proxies(self, env_name: str) -> List[str]:
        """Loads and cleans proxy URLs from environment variables or global ALL_PROXY."""
        raw = os.getenv(env_name, "").strip()
        if not raw:
            try:
                import config
                cfg_val = getattr(config, env_name, None)
                if cfg_val:
                    if isinstance(cfg_val, list):
                        return [str(p).strip() for p in cfg_val if str(p).strip()]
                    elif isinstance(cfg_val, str):
                        raw = cfg_val.strip()
            except Exception:
                pass

        if not raw:
            global_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy", "")
            return [global_proxy.strip()] if global_proxy.strip() else []
            
        return [p.strip() for p in raw.split(",") if p.strip()]

    def check_socket_active(self, proxy_url_str: str) -> bool:
        """Performs a quick TCP connection test to verify if the proxy server is reachable."""
        if not proxy_url_str:
            return False
        try:
            parsed = parse_proxy_url(proxy_url_str)
            if not parsed:
                return False
            host = parsed.get("host")
            port = parsed.get("port")
            if not host or not port:
                return False
            with socket.create_connection((host, int(port)), timeout=self.test_timeout):
                return True
        except Exception as e:
            logger.debug(f"Socket connection check failed for {proxy_url_str}: {str(e)}")
            return False

    def get_proxy(self, category: str) -> Optional[str]:
        """Returns the active reachable proxy for the specified subsystem category."""
        self.reload_proxies()
        proxies = self.categories.get(category, [])
        if not proxies:
            return None

        start_idx = self.indexes.get(category, 0)
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
                    self.indexes[category] = idx
                    return proxy

        return proxies[0] if not self.strict_check else None

    def get_telethon_proxy_settings(self) -> Tuple[Any, Any]:
        """
        Resolves proxy parameters and custom connection class for Telethon.
        Supports MTProto Proxy (tg://proxy, https://t.me/proxy, mtproto://) and SOCKS5/HTTP.
        Returns: (proxy_tuple_or_dict, connection_class_or_None)
        """
        proxy_url = self.get_proxy("telegram")
        if not proxy_url:
            return None, None

        parsed = parse_proxy_url(proxy_url)
        if not parsed:
            logger.error(f"Failed to parse Telegram proxy URL: {proxy_url}")
            return None, None

        p_type = parsed.get("type", "socks5")
        host = parsed.get("host", "127.0.0.1")
        port = int(parsed.get("port", 10808))

        # 1. MTProto Proxy (tg://proxy, https://t.me/proxy, mtproto://, mtproxy://)
        if p_type == "mtproxy":
            try:
                from telethon.network import connection
                secret = parsed.get("secret", "")
                proxy_tuple = (host, port, secret)
                connection_class = connection.ConnectionTcpMTProxyRandomizedIntermediate
                logger.info(f"Telethon configured with MTProto Proxy: {host}:{port}")
                return proxy_tuple, connection_class
            except Exception as e:
                logger.error(f"Error initializing Telethon MTProxy connection class: {str(e)}")
                return None, None

        # 2. SOCKS5, SOCKS4, HTTP Proxy via PySocks
        socks_type = socks.SOCKS5 if p_type == "socks5" else (socks.SOCKS4 if p_type == "socks4" else socks.HTTP)
        proxy_dict = {
            'proxy_type': socks_type,
            'addr': host,
            'port': port,
            'rdns': True
        }
        if parsed.get("username"):
            proxy_dict['username'] = parsed["username"]
        if parsed.get("password"):
            proxy_dict['password'] = parsed["password"]

        logger.info(f"Telethon configured with {p_type.upper()} Proxy: {host}:{port}")
        return proxy_dict, None

    def get_telethon_proxy(self) -> Optional[Any]:
        """Backward compatibility helper: returns only the proxy structure."""
        proxy_param, _ = self.get_telethon_proxy_settings()
        return proxy_param


# Global singleton instance
proxy_rotator = ProxyRotationManager()
