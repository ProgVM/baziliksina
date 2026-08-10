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
from permission_manager import permission_manager
from service_manager import service_manager
from command_manager import command_manager

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
        self.app.router.add_route("*", "/site/{site_name}", self.handle_dynamic_site_request)
        self.app.router.add_route("*", "/site/{site_name}/{module_name:.*}", self.handle_dynamic_site_request)

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

        # User Ranks REST Endpoints
        self.app.router.add_get("/api/ranks", self.api_get_ranks)
        self.app.router.add_post("/api/ranks/set", self.api_set_rank)
        self.app.router.add_delete("/api/ranks/{user_id}", self.api_delete_rank)

        # Custom Commands REST Endpoints
        self.app.router.add_get("/api/commands", self.api_get_commands)
        self.app.router.add_post("/api/commands/add", self.api_add_command)
        self.app.router.add_delete("/api/commands/{name}", self.api_delete_command)
        self.app.router.add_post("/api/command/execute", self.api_execute_command)

        # Services REST Endpoints
        self.app.router.add_get("/api/services", self.api_get_services)
        self.app.router.add_post("/api/services/add", self.api_add_service)
        self.app.router.add_post("/api/services/start/{name}", self.api_start_service)
        self.app.router.add_post("/api/services/stop/{name}", self.api_stop_service)
        self.app.router.add_delete("/api/services/{name}", self.api_delete_service)

        # Cron Jobs REST Endpoints
        self.app.router.add_get("/api/cron", self.api_get_cron)
        self.app.router.add_post("/api/cron/add", self.api_add_cron)
        self.app.router.add_post("/api/cron/start/{name}", self.api_start_cron)
        self.app.router.add_post("/api/cron/stop/{name}", self.api_stop_cron)
        self.app.router.add_delete("/api/cron/{name}", self.api_delete_cron)

        # Dynamic Sites REST Endpoints
        self.app.router.add_get("/api/sites", self.api_get_sites)
        self.app.router.add_post("/api/sites/add", self.api_add_site)
        self.app.router.add_get("/api/sites/details/{name}", self.api_get_site_details)
        self.app.router.add_delete("/api/sites/delete/{name}", self.api_delete_site)
        self.app.router.add_get("/api/sites/logs/{name}", self.api_get_site_logs)
        self.app.router.add_post("/api/sites/command/{name}", self.api_run_site_command)

        # System Endpoints
        self.app.router.add_get("/api/system/stats", self.api_system_stats)
        self.app.router.add_get("/api/system/logs", self.api_system_logs)
        self.app.router.add_post("/api/system/restart", self.api_system_restart)

    # =====================================================================
    # USER RANKS API
    # =====================================================================
    @auth_required
    async def api_get_ranks(self, request):
        try:
            async with self.db.db.execute("SELECT user_id_or_name, rank, permissions_json FROM user_ranks") as cursor:
                rows = await cursor.fetchall()
                results = [{"user_id_or_name": r[0], "rank": r[1], "permissions": json.loads(r[2]) if r[2] else []} for r in rows]
                return web.json_response({"status": "success", "count": len(results), "ranks": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_set_rank(self, request):
        try:
            data = await request.json()
            target_user = data.get("user_id") or data.get("username")
            rank = data.get("rank")
            perms = data.get("permissions")
            if not target_user or rank is None:
                return web.json_response({"status": "error", "message": "Missing 'user_id' or 'rank'."}, status=400)

            await self.db.save_user_rank(target_user, int(rank), perms)
            return web.json_response({"status": "success", "message": f"Rank for '{target_user}' set to {rank}."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_rank(self, request):
        try:
            user_id = request.match_info["user_id"]
            deleted = await self.db.delete_user_rank(user_id)
            return web.json_response({"status": "success" if deleted else "error", "message": f"Rank for '{user_id}' reset."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    # =====================================================================
    # CUSTOM COMMANDS API
    # =====================================================================
    @auth_required
    async def api_get_commands(self, request):
        try:
            cmds = await self.db.get_all_custom_commands()
            return web.json_response({"status": "success", "count": len(cmds), "commands": cmds})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_add_command(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            code = data.get("code")
            help_text = data.get("help_text", "Custom command")
            category = data.get("category", "general")
            if not name or not code:
                return web.json_response({"status": "error", "message": "Missing 'name' or 'code'."}, status=400)

            await self.db.save_custom_command(name, code, help_text=help_text, category=category)
            return web.json_response({"status": "success", "message": f"Command /{name.lstrip('/')} created."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_command(self, request):
        try:
            name = request.match_info["name"]
            deleted = await self.db.delete_custom_command(name)
            return web.json_response({"status": "success" if deleted else "error", "message": f"Command /{name} deleted."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_execute_command(self, request):
        try:
            data = await request.json()
            command_text = data.get("command")
            user_id = data.get("user_id", config.OWNER_ID)
            chat_id = data.get("chat_id", config.OWNER_ID)
            if not command_text:
                return web.json_response({"status": "error", "message": "Missing 'command' parameter."}, status=400)

            res = await command_manager.execute_pipeline(command_text, int(user_id), int(chat_id))
            return web.json_response({"status": "success", "result": res})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    # =====================================================================
    # SERVICES & CRON JOBS API
    # =====================================================================
    @auth_required
    async def api_get_services(self, request):
        services = service_manager.list_services()
        return web.json_response({"status": "success", "count": len(services), "services": services})

    @auth_required
    async def api_add_service(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            code = data.get("code")
            desc = data.get("description", "Custom Service")
            status = data.get("status", "stopped")
            if not name or not code:
                return web.json_response({"status": "error", "message": "Missing 'name' or 'code'."}, status=400)

            await self.db.save_custom_service(name, code, description=desc, status=status)
            service_manager.register_service(name, code, description=desc, is_custom=True, status=status)
            if status == "running":
                await service_manager.start_service(name)
            return web.json_response({"status": "success", "message": f"Service '{name}' created."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_start_service(self, request):
        try:
            name = request.match_info["name"]
            ok = await service_manager.start_service(name)
            return web.json_response({"status": "success" if ok else "error", "message": f"Service '{name}' started."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_stop_service(self, request):
        try:
            name = request.match_info["name"]
            ok = await service_manager.stop_service(name)
            return web.json_response({"status": "success" if ok else "error", "message": f"Service '{name}' stopped."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_service(self, request):
        try:
            name = request.match_info["name"]
            await service_manager.stop_service(name)
            deleted = await self.db.delete_custom_service(name)
            return web.json_response({"status": "success" if deleted else "error", "message": f"Service '{name}' deleted."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_cron(self, request):
        cron_jobs = service_manager.list_cron_jobs()
        return web.json_response({"status": "success", "count": len(cron_jobs), "cron_jobs": cron_jobs})

    @auth_required
    async def api_add_cron(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            schedule_spec = data.get("schedule_spec")
            code = data.get("code")
            desc = data.get("description", "Custom Cron Job")
            status = data.get("status", "active")
            if not name or not schedule_spec or not code:
                return web.json_response({"status": "error", "message": "Missing 'name', 'schedule_spec', or 'code'."}, status=400)

            await self.db.save_custom_cron_job(name, schedule_spec, code, description=desc, status=status)
            service_manager.register_cron_job(name, schedule_spec, code, description=desc, is_custom=True, status=status)
            if status == "active":
                await service_manager.start_cron_job(name)
            return web.json_response({"status": "success", "message": f"Cron job '{name}' created."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_start_cron(self, request):
        try:
            name = request.match_info["name"]
            ok = await service_manager.start_cron_job(name)
            return web.json_response({"status": "success" if ok else "error", "message": f"Cron job '{name}' started."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_stop_cron(self, request):
        try:
            name = request.match_info["name"]
            ok = await service_manager.stop_cron_job(name)
            return web.json_response({"status": "success" if ok else "error", "message": f"Cron job '{name}' stopped."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_cron(self, request):
        try:
            name = request.match_info["name"]
            await service_manager.stop_cron_job(name)
            deleted = await self.db.delete_custom_cron_job(name)
            return web.json_response({"status": "success" if deleted else "error", "message": f"Cron job '{name}' deleted."})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    # =====================================================================
    # DYNAMIC SITES API
    # =====================================================================
    @auth_required
    async def api_get_sites(self, request):
        try:
            sites = await self.db.get_all_dynamic_sites()
            results = []
            now = int(time.time())
            for s in sites:
                name = s["name"]
                site_dir = config.WORKSPACE_DIR / "sites" / name
                size = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file()) if site_dir.exists() else 0
                expires_at = s["expires_at"]
                remaining = max(0, expires_at - now) if expires_at else None
                    
                results.append({
                    "name": name,
                    "status": s["status"],
                    "created_at": s["created_at"],
                    "expires_at": expires_at,
                    "expires_in_seconds": remaining,
                    "storage_size_bytes": size
                })
            return web.json_response({"status": "success", "count": len(results), "sites": results})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_add_site(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            config_dict = data.get("config")
            modules_list = data.get("modules")
            expires_in_seconds = data.get("expires_in_seconds")
            
            if not name or not config_dict:
                return web.json_response({"status": "error", "message": "Missing 'name' or 'config' dictionary."}, status=400)
            
            from tools.site_tools import toolkit_sites
            res_msg = await toolkit_sites.create_or_update_site(
                name=name,
                config_dict=config_dict,
                modules_list=modules_list,
                expires_in_seconds=expires_in_seconds
            )
            
            if "Success" in res_msg:
                return web.json_response({"status": "success", "message": res_msg})
            else:
                return web.json_response({"status": "error", "message": res_msg}, status=400)
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_site_details(self, request):
        try:
            name = request.match_info["name"]
            clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
            if clean_name != name.lower():
                return web.json_response({"status": "error", "message": "Invalid site name format."}, status=400)
                
            site_data = await self.db.get_dynamic_site(clean_name)
            if not site_data:
                return web.json_response({"status": "error", "message": f"Site '{clean_name}' not found."}, status=404)
                
            site_config = json.loads(site_data["config_json"])
            modules = json.loads(site_data["modules_json"])
                
            site_dir = config.WORKSPACE_DIR / "sites" / clean_name
            size = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file()) if site_dir.exists() else 0
                
            return web.json_response({
                "status": "success",
                "name": clean_name,
                "site_status": site_data["status"],
                "created_at": site_data["created_at"],
                "expires_at": site_data["expires_at"],
                "storage_size_bytes": size,
                "config": site_config,
                "modules": modules
            })
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_delete_site(self, request):
        try:
            name = request.match_info["name"]
            clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
            if clean_name != name.lower():
                return web.json_response({"status": "error", "message": "Invalid site name format."}, status=400)
                
            from tools.site_tools import toolkit_sites
            res_msg = await toolkit_sites.delete_site(clean_name)
            if "Success" in res_msg:
                return web.json_response({"status": "success", "message": res_msg})
            else:
                return web.json_response({"status": "error", "message": res_msg}, status=404)
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_get_site_logs(self, request):
        try:
            name = request.match_info["name"]
            clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
            if clean_name != name.lower():
                return web.json_response({"status": "error", "message": "Invalid site name format."}, status=400)
                
            site_dir = config.WORKSPACE_DIR / "sites" / clean_name
            log_file = site_dir / "site.log"
            if not log_file.exists():
                return web.json_response({"status": "success", "name": clean_name, "logs_count": 0, "logs": []})
                
            limit = int(request.query.get("limit", 150))
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            logs = [l.strip() for l in lines[-limit:] if l.strip()]
                
            return web.json_response({
                "status": "success",
                "name": clean_name,
                "logs_count": len(logs),
                "logs": logs
            })
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @auth_required
    async def api_run_site_command(self, request):
        try:
            name = request.match_info["name"]
            clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
            if clean_name != name.lower():
                return web.json_response({"status": "error", "message": "Invalid site name format."}, status=400)
            site_dir = config.WORKSPACE_DIR / "sites" / clean_name
            if not site_dir.exists():
                return web.json_response({"status": "error", "message": f"Site '{clean_name}' does not exist."}, status=404)
            data = await request.json()
            command = data.get("command")
            if not command:
                return web.json_response({"status": "error", "message": "Missing 'command' parameter."}, status=400)
            from tools.site_tools import check_site_command_allowed
            if not check_site_command_allowed(command):
                return web.json_response({"status": "error", "message": "Command blocked by security policy."}, status=403)
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(site_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            res = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
            return web.json_response({"status": "success", "output": res})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

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

    async def handle_dynamic_site_request(self, request):
        import time
        site_name = request.match_info.get("site_name")
        module_name = request.match_info.get("module_name", "index")
        
        host_header = request.headers.get("Host", "")
        if not site_name and host_header:
            parts = host_header.split(":")[0].split(".")
            if len(parts) > 2:
                possible_site = parts[0]
                site_data = await self.db.get_dynamic_site(possible_site)
                if site_data:
                    site_name = possible_site
                    module_name = request.path.lstrip("/")
        
        if not site_name:
            return web.json_response({"status": "error", "message": "Site not specified."}, status=400)
            
        clean_site_name = "".join(c for c in site_name if c.isalnum() or c in ["_", "-"]).strip().lower()
        if clean_site_name != site_name.lower():
            return web.json_response({"status": "error", "message": "Invalid site name format."}, status=400)

        if request.path == f"/site/{clean_site_name}":
            return web.HTTPPermanentRedirect(f"/site/{clean_site_name}/")

        if not module_name or module_name == "/":
            module_name = "index"

        site_data = await self.db.get_dynamic_site(clean_site_name)
        if not site_data:
            return web.json_response({"status": "error", "message": f"Site '{clean_site_name}' not found."}, status=404)
            
        if site_data.get("status") != "active":
            return web.json_response({"status": "error", "message": f"Site '{clean_site_name}' is stopped or disabled."}, status=403)

        now = int(time.time())
        expires_at = site_data.get("expires_at")
        if expires_at and now > expires_at:
            await self.db.delete_dynamic_site(clean_site_name)
            import shutil
            site_dir = config.WORKSPACE_DIR / "sites" / clean_site_name
            if site_dir.exists():
                shutil.rmtree(site_dir)
            return web.json_response({"status": "error", "message": "Site has expired and was removed."}, status=410)

        try:
            site_config = json.loads(site_data["config_json"])
            modules = json.loads(site_data["modules_json"])
        except Exception as err:
            return web.json_response({"status": "error", "message": f"Failed to parse site configuration: {str(err)}"}, status=500)

        client_ip = request.headers.get("CF-Connecting-IP") or \
                    request.headers.get("X-Real-IP") or \
                    request.headers.get("X-Forwarded-For")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            peername = request.transport.get_extra_info('peername')
            client_ip = peername[0] if peername else "127.0.0.1"

        allowed_ips = site_config.get("allowed_ips", "all")
        if allowed_ips != "all":
            allowed_list = [ip.strip() for ip in allowed_ips.split(",") if ip.strip()]
            if client_ip not in allowed_list:
                return web.json_response({"status": "error", "message": "Access denied by IP policy."}, status=403)

        allowed_methods_raw = site_config.get("allowed_methods", config.SITE_ALLOWED_METHODS_DEFAULT)
        blocked_methods_raw = site_config.get("blocked_methods", config.SITE_BLOCKED_METHODS_DEFAULT)
        
        allowed_methods = [m.strip() for m in allowed_methods_raw.split(",") if m.strip()] if isinstance(allowed_methods_raw, str) else (allowed_methods_raw or [])
        blocked_methods = [m.strip() for m in blocked_methods_raw.split(",") if m.strip()] if isinstance(blocked_methods_raw, str) else (blocked_methods_raw or [])
        
        from utils import matches_filter
        if not matches_filter(request.method, allowed_methods, blocked_methods):
            return web.json_response({"status": "error", "message": f"Method {request.method} is blocked by site policy."}, status=405)

        max_size = int(site_config.get("max_request_size", config.SITE_MAX_REQUEST_SIZE_DEFAULT))
        if request.content_length and request.content_length > max_size:
            return web.json_response({"status": "error", "message": "Request entity too large."}, status=413)

        module_obj = None
        req_clean = module_name.strip().lower().replace("\\", "/").lstrip("/").rstrip("/")
        if not req_clean:
            req_clean = "index"

        req_with_py = req_clean if req_clean.endswith(".py") else f"{req_clean}.py"
        req_no_py = req_clean[:-3] if req_clean.endswith(".py") else req_clean

        for mod in modules:
            m_path = mod.get("path", "").strip().lower().replace("\\", "/").lstrip("/").rstrip("/")
            if not m_path:
                continue
            m_with_py = m_path if m_path.endswith(".py") else f"{m_path}.py"
            m_no_py = m_path[:-3] if m_path.endswith(".py") else m_path

            if m_path == req_clean or m_with_py == req_with_py or m_no_py == req_no_py:
                module_obj = mod
                break
                
        if not module_obj:
            return web.json_response({"status": "error", "message": f"Module '{module_name}' not found on site '{clean_site_name}'."}, status=404)

        code = module_obj.get("code", "")
        code = code.replace("\\\\r\\\\n", "\n").replace("\\\\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
        execution_timeout = float(site_config.get("timeout", config.SITE_TIMEOUT_DEFAULT))
        
        req_params = dict(request.query)
        req_body = ""
        req_json = {}
        req_form = {}
        if request.can_read_body:
            try:
                if "application/json" in request.content_type:
                    req_json = await request.json()
                    req_body = json.dumps(req_json)
                elif "application/x-www-form-urlencoded" in request.content_type:
                    req_body = await request.text()
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(req_body)
                    req_form = {k: v[0] if isinstance(v, list) and v else v for k, v in parsed.items()}
                else:
                    req_body = await request.text()
            except Exception:
                pass

        site_prefix = f"/site/{clean_site_name}"
        base_url = f"{request.scheme}://{request.host}{site_prefix}"

        allowed_imports_raw = site_config.get("allowed_imports", config.SITE_ALLOWED_IMPORTS_DEFAULT)
        blocked_imports_raw = site_config.get("blocked_imports", config.SITE_BLOCKED_IMPORTS_DEFAULT)
        
        allowed_imports = [imp.strip() for imp in allowed_imports_raw.split(",") if imp.strip()] if isinstance(allowed_imports_raw, str) else (allowed_imports_raw or [])
        blocked_imports = [imp.strip() for imp in blocked_imports_raw.split(",") if imp.strip()] if isinstance(blocked_imports_raw, str) else (blocked_imports_raw or [])
        
        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            root_name = name.split(".")[0]
            from utils import matches_filter
            if not matches_filter(root_name, allowed_imports, blocked_imports):
                raise ImportError(f"Security Policy Error: Import of module '{name}' is restricted for this site.")
            allowed_site_py = [i.strip() for i in config.SITE_PYTHON_WHITELIST.split(",") if i.strip()] if isinstance(config.SITE_PYTHON_WHITELIST, str) else config.SITE_PYTHON_WHITELIST
            blocked_site_py = [b.strip() for b in config.SITE_PYTHON_BLACKLIST.split(",") if b.strip()] if isinstance(config.SITE_PYTHON_BLACKLIST, str) else config.SITE_PYTHON_BLACKLIST
            if not matches_filter(root_name, allowed_site_py, blocked_site_py):
                raise ImportError(f"Security Policy Error: Import of module '{name}' is blocked by server policy.")
            return __import__(name, globals, locals, fromlist, level)

        site_dir = config.WORKSPACE_DIR / "sites" / clean_site_name
        site_dir.mkdir(parents=True, exist_ok=True)

        def safe_site_open(file, mode='r', *args, **kwargs):
            if not os.path.isabs(file):
                file = site_dir / file
            resolved = Path(file).resolve()
            if not str(resolved).startswith(str(site_dir.resolve())):
                raise PermissionError("Security Policy Error: Attempted to access a directory outside the site isolated workspace.")

            return open(resolved, mode, *args, **kwargs)

        def site_print(*args):
            log_line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + " ".join(str(a) for a in args) + "\n"
            try:
                log_file = site_dir / "site.log"
                if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    with open(log_file, "w", encoding="utf-8") as lf:
                        lf.writelines(lines[-500:])
                with open(log_file, "a", encoding="utf-8") as lf:
                    lf.write(log_line)
            except Exception:
                pass
            logger.info(f"[Site '{clean_site_name}' Print]: " + " ".join(str(a) for a in args))

        local_vars = {
            "__import__": safe_import,
            "open": safe_site_open,
            "request": {
                "method": request.method,
                "headers": dict(request.headers),
                "query": req_params,
                "body": req_body,
                "json": req_json,
                "form": req_form,
                "client_ip": client_ip,
                "cookies": dict(request.cookies),
                "site_prefix": site_prefix,
                "base_url": base_url,
                "module_path": module_name
            },
            "print": site_print,
            "response": {
                "status": 200,
                "body": "Hello from Baziliksina Dynamic Site!",
                "headers": {"Content-Type": "text/html; charset=utf-8"}
            }
        }

        allowed_globals = site_config.get("allowed_globals", [])
        if "db" in allowed_globals:
            local_vars["db"] = self.db
        if "client" in allowed_globals:
            from sandbox import SandboxedClient
            local_vars["client"] = SandboxedClient(self.client, site_dir)

        for k, v in site_config.get("env_variables", {}).items():
            local_vars[k] = v

        try:
            import ast
            import types
            compiled_code = compile(code, f"<site_{clean_site_name}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            
            async def run_compiled():
                res = eval(compiled_code, local_vars, local_vars)
                if isinstance(res, types.CoroutineType):
                    await res
            await asyncio.wait_for(run_compiled(), timeout=execution_timeout)
            
            entrypoint = None
            for name in ["handle", "handler", "main", "index", "get", "post"]:
                if name in local_vars and callable(local_vars[name]):
                    entrypoint = local_vars[name]
                    break
            if entrypoint:
                import inspect
                if inspect.iscoroutinefunction(entrypoint):
                    res_val = await entrypoint(local_vars["request"])
                else:
                    res_val = entrypoint(local_vars["request"])
                if res_val:
                    local_vars["response"] = res_val
            
            resp_data = local_vars.get("response", {})
            if isinstance(resp_data, web.StreamResponse):
                return resp_data

            if isinstance(resp_data, str):
                resp_data = {"status": 200, "body": resp_data, "headers": {"Content-Type": "text/html"}}
            elif isinstance(resp_data, dict):
                if "body" not in resp_data:
                    resp_data["body"] = json.dumps(resp_data)
                    resp_data["headers"] = {"Content-Type": "application/json"}
            else:
                resp_data = {"status": 200, "body": str(resp_data), "headers": {"Content-Type": "text/plain"}}

            status = resp_data.get("status", 200)
            body = resp_data.get("body", "")
            headers = dict(resp_data.get("headers", {}))
            
            for kh, vh in site_config.get("custom_headers", {}).items():
                headers[kh] = vh

            content_type = headers.pop("Content-Type", "text/html")
            
            charset = None
            if ";" in content_type:
                parts = content_type.split(";")
                content_type = parts[0].strip()
                for p in parts[1:]:
                    if "charset=" in p.lower():
                        charset = p.lower().split("charset=")[1].strip()
            
            return web.Response(text=body, status=status, headers=headers, content_type=content_type, charset=charset)

        except asyncio.TimeoutError:
            timeout_msg = f"Execution timeout of {execution_timeout}s exceeded."
            site_print(f"TIMEOUT EXCEEDED: {timeout_msg}")
            return web.json_response({"status": "error", "message": timeout_msg}, status=504)
        except Exception as exec_err:
            import traceback
            tb_str = traceback.format_exc()
            site_print(f"EXCEPTION CRASH:\n{tb_str}")
            return web.json_response({"status": "error", "message": f"Module Execution Error: {str(exec_err)}"}, status=500)

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
        os.execv(sys.executable, [sys.executable] + sys.argv)


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
