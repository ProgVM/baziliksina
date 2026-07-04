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

# Setup startup timestamp for uptime calculations
START_TIME = int(time.time())
active_runner = None


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
    async def wrapper(*args, **kwargs):
        request = args[-1]
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"status": "error", "message": "Unauthorized. Bearer token missing."}, status=401)
        
        token = auth_header[7:].strip()
        if token not in config.WEB_SERVER_API_KEYS:
            return web.json_response({"status": "error", "message": "Forbidden. Invalid access token."}, status=403)
        
        request["client_scope"] = config.WEB_SERVER_API_KEYS[token]
        return await func(*args, **kwargs)
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
        self.app.router.add_delete("/api/keys/delete", self.api_delete_key)
        self.app.router.add_post("/api/keys/rotate", self.api_rotate_key)
        
        self.app.router.add_get("/api/db/stats", self.api_db_stats)
        self.app.router.add_post("/api/db/query", self.api_db_query)
        self.app.router.add_get("/api/db/export", self.api_db_export)
        self.app.router.add_get("/api/db/history/{chat_id}", self.api_get_chat_history)
        
        self.app.router.add_get("/api/meta/users", self.api_get_meta_users)
        self.app.router.add_get("/api/meta/chats", self.api_get_meta_chats)
        
        self.app.router.add_get("/api/timers", self.api_get_timers)
        self.app.router.add_post("/api/timers/add", self.api_add_timer)
        self.app.router.add_delete("/api/timers/{id}", self.api_delete_timer)
        
        self.app.router.add_get("/api/triggers/{chat_id}", self.api_get_triggers)
        self.app.router.add_post("/api/triggers/add", self.api_add_trigger)
        self.app.router.add_delete("/api/triggers/{id}", self.api_delete_trigger)
        
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
        try:
            data = await request.json()
            key_val = data.get("key")
            provider = data.get("provider")
            if not key_val or provider not in ["gemini", "pollinations"]:
                return web.json_response({"status": "error", "message": "Missing 'key' or invalid 'provider'."}, status=400)
            
            await self.db.save_key_meta(key_value=key_val, provider=provider)
            return web.json_response({"status": "success", "message": f"Key successfully added to {provider} pool."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_key(self, request):
        try:
            data = await request.json()
            key_val = data.get("key")
            if not key_val:
                return web.json_response({"status": "error", "message": "Missing 'key' parameter."}, status=400)
            await self.db.db.execute("DELETE FROM api_keys WHERE key_value = ?", (key_val,))
            await self.db.db.commit()
            return web.json_response({"status": "success", "message": "API key deleted from database."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_rotate_key(self, request):
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
    async def api_db_stats(self, request):
        """Asynchronously returns SQLite DB structure, size, and table row counts dynamically."""
        try:
            db_path = config.SAFE_DB_DIR / config.DB_NAME
            db_size = db_path.stat().st_size if db_path.exists() else 0
            
            table_stats = {}
            async with self.db.db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                tables = await cursor.fetchall()
                
            for (table_name,) in tables:
                async with self.db.db.execute(f"SELECT COUNT(*) FROM {table_name}") as size_cursor:
                    row_count = await size_cursor.fetchone()
                    table_stats[table_name] = row_count[0] if row_count else 0
                    
            return web.json_response({
                "status": "success",
                "database_size_bytes": db_size,
                "tables_count": len(table_stats),
                "statistics": table_stats
            })
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_db_query(self, request):
        try:
            data = await request.json()
            sql = data.get("sql")
            if not sql:
                return web.json_response({"status": "error", "message": "SQL statement is missing."}, status=400)
            
            async with self.db.db.execute(sql) as cursor:
                if cursor.description is not None:
                    rows = await cursor.fetchall()
                    cols = [d[0] for d in cursor.description]
                    limit = int(request.query.get("limit", config.SQL_SELECT_LIMIT))
                    results = [dict(zip(cols, row)) for row in rows[:limit]]
                    return web.json_response({"status": "success", "type": "SELECT", "rows_count": len(results), "data": results})
                else:
                    await self.db.db.commit()
                    return web.json_response({"status": "success", "type": "WRITE", "affected_rows": cursor.rowcount})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_db_export(self, request):
        db_path = config.SAFE_DB_DIR / config.DB_NAME
        if not db_path.exists():
            return web.json_response({"status": "error", "message": "DB file not found."}, status=404)
        return web.FileResponse(path=db_path, filename=config.DB_NAME)

    @auth_required
    async def api_get_chat_history(self, request):
        """Asynchronously exports dialog history with optional limits and offsets."""
        try:
            chat_id = request.match_info["chat_id"]
            limit = int(request.query.get("limit", 100))
            offset = int(request.query.get("offset", 0))
            
            async with self.db.db.execute(
                "SELECT role, text, media_info, timestamp FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (chat_id, limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                results = [{"role": r[0], "text": r[1], "media": r[2], "timestamp": r[3]} for r in rows]
                return web.json_response({"chat_id": chat_id, "messages_count": len(results), "messages": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_meta_users(self, request):
        """Returns cached metadata of Telegram users from the users_meta table."""
        try:
            limit = int(request.query.get("limit", config.WEB_SERVER_DEFAULT_META_LIMIT))
            async with self.db.db.execute("SELECT id, username, first_name, last_name, premium, verified FROM users_meta LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                results = [{"id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3], "premium": bool(r[4]), "verified": bool(r[5])} for r in rows]
                return web.json_response({"count": len(results), "users": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_meta_chats(self, request):
        """Returns cached metadata of Telegram chats/channels from the chats_meta table."""
        try:
            limit = int(request.query.get("limit", config.WEB_SERVER_DEFAULT_META_LIMIT))
            async with self.db.db.execute("SELECT id, title, username, type FROM chats_meta LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                results = [{"id": r[0], "title": r[1], "username": r[2], "type": r[3]} for r in rows]
                return web.json_response({"count": len(results), "chats": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_timers(self, request):
        """Lists all pending persistent timers."""
        try:
            timers = await self.db.get_pending_timers()
            results = [{"id": t[0], "chat_id": t[1], "execute_at": t[2], "action": t[3], "code": bool(t[4])} for t in timers]
            return web.json_response({"count": len(results), "timers": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_add_timer(self, request):
        try:
            data = await request.json()
            chat_id = data.get("chat_id")
            delay = int(data.get("delay", config.WEB_SERVER_DEFAULT_TIMER_DELAY))
            action = data.get("action", "Scheduled API Task")
            code = data.get("code")
            if not chat_id:
                return web.json_response({"status": "error", "message": "Missing 'chat_id'."}, status=400)
            await self.db.add_timer(str(chat_id), delay, action, code)
            return web.json_response({"status": "success", "message": "Timer set successfully."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_timer(self, request):
        try:
            timer_id = int(request.match_info["id"])
            await self.db.delete_timer(timer_id)
            return web.json_response({"status": "success", "message": f"Timer {timer_id} cancelled."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_triggers(self, request):
        try:
            chat_id = request.match_info["chat_id"]
            triggers = await self.db.get_active_triggers(chat_id)
            results = [{"id": t[0], "type": t[1], "value": t[2], "action": t[3], "code": bool(t[4])} for t in triggers]
            return web.json_response({"chat_id": chat_id, "count": len(results), "triggers": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_add_trigger(self, request):
        try:
            data = await request.json()
            chat_id = data.get("chat_id")
            t_type = data.get("type", "word")
            t_val = data.get("value")
            t_action = data.get("action", "Auto Trigger Task")
            code = data.get("code")
            if not chat_id or not t_val:
                return web.json_response({"status": "error", "message": "Missing 'chat_id' or 'value'."}, status=400)
            await self.db.add_trigger(str(chat_id), t_type, t_val, t_action, code)
            return web.json_response({"status": "success", "message": "Trigger added successfully."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_trigger(self, request):
        try:
            trigger_id = int(request.match_info["id"])
            await self.db.delete_trigger(trigger_id)
            return web.json_response({"status": "success", "message": f"Trigger {trigger_id} deleted."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_config(self, request):
        serialized_config = {
            k: str(v) for k, v in vars(config).items() if not k.startswith("_") and k.isupper()
        }
        return web.json_response(serialized_config)

    @auth_required
    async def api_update_config(self, request):
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
        name = request.match_info["name"]
        deleted = await self.db.delete_custom_tool(name)
        if deleted:
            registry.unregister(name)
            return web.json_response({"status": "success", "message": f"Custom tool {name} deleted."})
        return web.json_response({"status": "error", "message": f"Tool {name} not found or is a protected system tool."}, status=404)

    @auth_required
    async def api_sandbox_execute(self, request):
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
        log_path = Path(config.WEB_SERVER_LOG_PATH)
        if not log_path.exists():
            return web.json_response({"status": "error", "message": "Log file not found on disk."}, status=404)
        
        limit = int(request.query.get("limit", config.WEB_SERVER_DEFAULT_LOG_LIMIT))
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return web.json_response({"logs": lines[-limit:]})

    @auth_required
    async def api_system_restart(self, request):
        logger.warning("Dynamic reboot requested via secure Web-API...")
        asyncio.create_task(self._reboot_sequence())
        return web.json_response({"status": "success", "message": "Reboot sequence initiated."})

    async def _reboot_sequence(self):
        await asyncio.sleep(config.WEB_SERVER_REBOOT_DELAY)
        if self.db:
            await self.db.close()
        os.execv(sys.executable, ['python'] + sys.argv)


async def start_web_server(telegram_client, db_manager, ai_manager):
    """Initializes and runs the web server in the background loop."""
    global active_runner
    if not config.WEB_SERVER_ENABLE:
        logger.info("Built-in Web Server is disabled by config.")
        return

    host = config.WEB_SERVER_HOST
    if not host or host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((config.WEB_SERVER_IP_DETECTION_HOST, config.WEB_SERVER_IP_DETECTION_PORT))
            host = s.getsockname()[0]
            s.close()
        except Exception:
            host = "127.0.0.1"
        logger.info(f"WEB_SERVER_HOST not specified. Auto-detected IP address: {host}")

    server_obj = BaziliksinaWebServer(telegram_client, db_manager, ai_manager)
    runner = web.AppRunner(server_obj.app)
    await runner.setup()
    active_runner = runner
    site = web.TCPSite(runner, host, config.WEB_SERVER_PORT)
    await site.start()
    
    # Dynamic host resolution (supports empty subdomains seamlessly)
    if config.WEB_SERVER_SUBDOMAIN:
        host_str = f"http://{config.WEB_SERVER_SUBDOMAIN}.{host}:{config.WEB_SERVER_PORT}"
    else:
        host_str = f"http://{host}:{config.WEB_SERVER_PORT}"
        
    logger.info(f"Built-in Secure Web Server successfully started on {host_str}")

async def stop_web_server():
    """Cleanly shuts down the web server runner and releases sockets."""
    global active_runner
    if active_runner:
        try:
            logger.info("Shutting down Web Server...")
            await active_runner.cleanup()
        except Exception as e:
            logger.error(f"Error shutting down Web Server: {str(e)}")
