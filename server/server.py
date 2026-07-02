# server/server.py
import os
import sys
import json
import logging
import asyncio
import time
from pathlib import Path
from aiohttp import web

# Resolve correct project root path imports
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import config
from db_manager import DBManager
from registry import registry

logger = logging.getLogger("WebServer")

active_runner = None

# Setup startup timestamp for uptime calculations
START_TIME = int(time.time())

@web.middleware
async def ip_acl_middleware(request, handler):
    """Asynchronous middleware to enforce IP Access Control Whitelist verification on network level."""
    if config.WEB_SERVER_IP_ACL:
        peername = request.transport.get_extra_info('peername')
        if peername:
            client_ip, _ = peername
            if client_ip not in config.WEB_SERVER_IP_ACL and "all" not in config.WEB_SERVER_IP_ACL:
                logger.warning(f"Rejected connection attempt from unauthorized IP: {client_ip}")
                return web.json_response({"status": "error", "message": f"Forbidden. IP {client_ip} is not whitelisted."}, status=403)
    return await handler(request)


def auth_required(func):
    """Decorator to enforce secure Authorization: Bearer token verification with privileges validation."""
    async def wrapper(request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"status": "error", "message": "Unauthorized. Bearer token missing."}, status=401)
        
        token = auth_header[7:].strip()
        if token not in config.WEB_SERVER_API_KEYS:
            return web.json_response({"status": "error", "message": "Forbidden. Invalid access token."}, status=403)
        
        request["client_scope"] = config.WEB_SERVER_API_KEYS[token]
        return await func(request)
    return wrapper


class BaziliksinaWebServer:
    def __init__(self, telegram_client, db_manager, ai_manager):
        self.client = telegram_client
        self.db = db_manager
        self.ai = ai_manager
        self.app = web.Application(middlewares=[ip_acl_middleware])
        self._setup_routes()

    def _setup_routes(self):
        # 1. Public endpoints (No authorization token required)
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/ping", self.handle_ping)

        # 2. Private endpoints (Bearer token authorization required)
        self.app.router.add_get("/api/keys", self.api_get_keys)
        self.app.router.add_post("/api/keys/add", self.api_add_key)
        self.app.router.add_post("/api/keys/rotate", self.api_rotate_key)
        self.app.router.add_post("/api/db/query", self.api_db_query)
        self.app.router.add_get("/api/db/export", self.api_db_export)
        self.app.router.add_get("/api/config", self.api_get_config)
        self.app.router.add_put("/api/config/update", self.api_update_config)
        self.app.router.add_get("/api/prompts/{filename}", self.api_get_prompt)
        self.app.router.add_put("/api/prompts/{filename}", self.api_update_prompt)
        self.app.router.add_get("/api/tools", self.api_get_tools)
        self.app.router.add_delete("/api/tools/{name}", self.api_delete_tool)
        self.app.router.add_post("/api/sandbox/execute", self.api_sandbox_execute)
        self.app.router.add_get("/api/system/stats", self.api_system_stats)
        self.app.router.add_get("/api/system/logs", self.api_system_logs)
        self.app.router.add_post("/api/system/restart", self.api_system_restart)

    # =====================================================================
    # PUBLIC ENDPOINT HANDLERS
    # =====================================================================
    async def handle_index(self, request):
        """Serves an informative system overview landing page."""
        uptime = int(time.time()) - START_TIME
        bot_username = "unknown"
        if self.client:
            try:
                me = await self.client.get_me()
                bot_username = me.username or "baziliksina"
            except Exception:
                pass

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Baziliksina Assistant Dashboard 🌸</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f13; color: #f1f1f5; margin: 0; padding: 40px; }}
                .card {{ background: #1a1a24; border-radius: 12px; padding: 30px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); max-width: 600px; margin: auto; }}
                h1 {{ color: #ff85a2; margin-top: 0; }}
                .status {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; background: #31d082; color: #fff; }}
                .link {{ color: #ff85a2; text-decoration: none; font-weight: bold; }}
                .link:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Baziliksina Assistant Dashboard 🌸</h1>
                <p>System status: <span class="status">ONLINE</span></p>
                <p>Uptime: <b>{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s</b></p>
                <p>Active Telegram username: <a class="link" href="https://t.me/{bot_username}">@{bot_username}</a></p>
                <p>Secure Web Server Interface loaded and operational.</p>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html_content, content_type="text/html")

    async def handle_health(self, request):
        """Monitors Telegram network connection and SQLite DB availability."""
        telegram_active = self.client.is_connected() if self.client else False
        db_active = self.db.db is not None if self.db else False
        
        status_code = 200 if (telegram_active and db_active) else 503
        return web.json_response({
            "status": "success" if status_code == 200 else "unhealthy",
            "telegram_connected": telegram_active,
            "database_active": db_active
        }, status=status_code)

    async def handle_ping(self, request):
        return web.json_response({"ping": "pong"})

    # =====================================================================
    # PRIVATE API HANDLERS (AUTHENTICATED)
    # =====================================================================
    @auth_required
    async def api_get_keys(self, request):
        """Returns metadata of all API keys and their active status."""
        gemini_keys = await self.db.get_keys_by_provider("gemini")
        pollinations_keys = await self.db.get_keys_by_provider("pollinations")
        return web.json_response({
            "gemini_keys_count": len(gemini_keys),
            "gemini_keys": gemini_keys,
            "pollinations_keys_count": len(pollinations_keys),
            "pollinations_keys": pollinations_keys
        })

    @auth_required
    async def api_add_key(self, request):
        """Adds a new key to the SQLite rotation pools."""
        try:
            data = await request.json()
            key_val = data.get("key")
            provider = data.get("provider")  # 'gemini' or 'pollinations'
            if not key_val or provider not in ["gemini", "pollinations"]:
                return web.json_response({"status": "error", "message": "Missing 'key' or invalid 'provider'."}, status=400)
            
            await self.db.save_key_meta(key_value=key_val, provider=provider)
            return web.json_response({"status": "success", "message": f"Key successfully added to {provider} pool."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_rotate_key(self, request):
        """Forces an instant dynamic rotation of the active Gemini/Pollinations keys."""
        try:
            data = await request.json()
            provider = data.get("provider")
            if provider == "gemini":
                await self.ai.key_manager.rotate_key_async()
                active_model = self.ai.key_manager.get_model()
                return web.json_response({"status": "success", "message": "Gemini key rotated.", "active_model": active_model})
            elif provider == "pollinations":
                await self.ai.pollinations_key_manager.rotate_key_async()
                return web.json_response({"status": "success", "message": "Pollinations key rotated."})
            return web.json_response({"status": "error", "message": "Invalid provider."}, status=400)
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_db_query(self, request):
        """Executes a custom raw SQL statement securely."""
        try:
            data = await request.json()
            sql = data.get("sql")
            if not sql:
                return web.json_response({"status": "error", "message": "SQL statement is missing."}, status=400)
            
            async with self.db.db.execute(sql) as cursor:
                if cursor.description is not None:
                    rows = await cursor.fetchall()
                    cols = [d[0] for d in cursor.description]
                    results = [dict(zip(cols, row)) for row in rows[:100]]
                    return web.json_response({"status": "success", "type": "SELECT", "rows_count": len(results), "data": results})
                else:
                    await self.db.db.commit()
                    return web.json_response({"status": "success", "type": "WRITE", "affected_rows": cursor.rowcount})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_db_export(self, request):
        """Downloads the complete binary DB file."""
        db_path = config.SAFE_DB_DIR / config.DB_NAME
        if not db_path.exists():
            return web.json_response({"status": "error", "message": "DB file not found."}, status=404)
        return web.FileResponse(path=db_path, filename=config.DB_NAME)

    @auth_required
    async def api_get_config(self, request):
        """Returns the active configuration parameters."""
        serialized_config = {
            k: str(v) for k, v in vars(config).items() if not k.startswith("_") and k.isupper()
        }
        return web.json_response(serialized_config)

    @auth_required
    async def api_update_config(self, request):
        """Modifies a configuration parameter inside RAM and records it to DB."""
        try:
            data = await request.json()
            for k, v in data.items():
                if k.isupper() and hasattr(config, k):
                    setattr(config, k, v)
                    await self.db.save_setting(k, v)
            return web.json_response({"status": "success", "message": "Config updated dynamically."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_prompt(self, request):
        """Reads a prompt template file."""
        filename = request.match_info["filename"]
        # Security sanity check
        if not filename.endswith(".txt") or "/" in filename or "\\" in filename:
            return web.json_response({"status": "error", "message": "Invalid filename."}, status=400)
        
        path = config.BASE_DIR / "config" / filename
        if not path.exists():
            return web.json_response({"status": "error", "message": "Prompt file not found."}, status=404)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return web.json_response({"filename": filename, "content": content})

    @auth_required
    async def api_update_prompt(self, request):
        """Saves updated templates to config/ directory."""
        try:
            filename = request.match_info["filename"]
            if not filename.endswith(".txt") or "/" in filename or "\\" in filename:
                return web.json_response({"status": "error", "message": "Invalid filename."}, status=400)
            
            data = await request.json()
            content = data.get("content")
            if content is None:
                return web.json_response({"status": "error", "message": "Content cannot be null."}, status=400)
            
            path = config.BASE_DIR / "config" / filename
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return web.json_response({"status": "success", "message": f"Prompt file {filename} updated."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_tools(self, request):
        """Lists all registered tools."""
        tools_list = []
        for tool in registry.get_all_tools():
            tools_list.append({
                "name": tool.name,
                "category": tool.category,
                "description": tool.description,
                "is_custom": tool.is_custom
            })
        return web.json_response({"tools_count": len(tools_list), "tools": tools_list})

    @auth_required
    async def api_delete_tool(self, request):
        """Removes a custom dynamic tool."""
        name = request.match_info["name"]
        deleted = await self.db.delete_custom_tool(name)
        if deleted:
            registry.unregister(name)
            return web.json_response({"status": "success", "message": f"Custom tool {name} deleted."})
        return web.json_response({"status": "error", "message": f"Tool {name} not found or is a protected system tool."}, status=404)

    @auth_required
    async def api_sandbox_execute(self, request):
        """Runs custom sandboxed code directly from the Web-panel."""
        try:
            data = await request.json()
            code = data.get("code")
            if not code:
                return web.json_response({"status": "error", "message": "Code is missing."}, status=400)
            
            from sandbox import AsyncSandbox
            sandbox = AsyncSandbox(config.WORKSPACE_DIR, self.client, self.db, self.ai)
            result = await sandbox.execute(code)
            return web.json_response({"status": "success", "result": result})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_system_stats(self, request):
        """Returns deep telemetry data."""
        uptime = int(time.time()) - START_TIME
        import resource
        mem_usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return web.json_response({
            "uptime_seconds": uptime,
            "memory_usage_kb": mem_usage,
            "pid": os.getpid(),
            "python_version": sys.version
        })

    @auth_required
    async def api_system_logs(self, request):
        """Reads the latest logs from file."""
        log_path = Path(config.WEB_SERVER_LOG_PATH)
        if not log_path.exists():
            return web.json_response({"status": "error", "message": "Log file not found on disk."}, status=404)
        
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return web.json_response({"logs": lines[-100:]}) # Return latest 100 lines

    @auth_required
    async def api_system_restart(self, request):
        """Triggers a clean dynamic reboot of the system."""
        logger.warning("Dynamic reboot requested via secure Web-API...")
        asyncio.create_task(self._reboot_sequence())
        return web.json_response({"status": "success", "message": "Reboot sequence initiated."})

    async def _reboot_sequence(self):
        await asyncio.sleep(2.0)
        if self.db:
            await self.db.close()
        os.execv(sys.executable, ['python'] + sys.argv)


async def start_web_server(telegram_client, db_manager, ai_manager):
    """Initializes and runs the web server in the background loop."""
    global active_runner
    if not config.WEB_SERVER_ENABLE:
        logger.info("Built-in Web Server is disabled by config.")
        return

    server_obj = BaziliksinaWebServer(telegram_client, db_manager, ai_manager)
    runner = web.AppRunner(server_obj.app)
    await runner.setup()
    active_runner = runner
    site = web.TCPSite(runner, config.WEB_SERVER_HOST, config.WEB_SERVER_PORT)
    await site.start()
    logger.info(f"Built-in Secure Web Server successfully started on http://{config.WEB_SERVER_HOST}:{config.WEB_SERVER_PORT}")

async def stop_web_server():
    """Cleanly shuts down the web server runner and releases sockets."""
    global active_runner
    if active_runner:
        try:
            logger.info("Shutting down Web Server...")
            await active_runner.cleanup()
        except Exception as e:
            logger.error(f"Error shutting down Web Server: {str(e)}")
