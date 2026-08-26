# tools/site_tools.py
import os
import json
import logging
import time
import shutil
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

import config
import tools
from utils import matches_filter

logger = logging.getLogger("Tools.Sites")


def check_site_command_allowed(command: str) -> bool:
    import re
    from utils import matches_filter
    if config.SITE_COMMAND_REGEX_BLACKLIST:
        pattern_black = re.compile(config.SITE_COMMAND_REGEX_BLACKLIST, re.IGNORECASE)
        if pattern_black.search(command):
            return False
    if config.SITE_COMMAND_REGEX_WHITELIST:
        pattern_white = re.compile(config.SITE_COMMAND_REGEX_WHITELIST, re.IGNORECASE)
        if not pattern_white.search(command):
            return False
    whitelist = [w.strip() for w in config.SITE_COMMAND_WHITELIST.split(",") if w.strip()] if isinstance(config.SITE_COMMAND_WHITELIST, str) else config.SITE_COMMAND_WHITELIST
    blacklist = [b.strip() for b in config.SITE_COMMAND_BLACKLIST.split(",") if b.strip()] if isinstance(config.SITE_COMMAND_BLACKLIST, str) else config.SITE_COMMAND_BLACKLIST
    return matches_filter(command, whitelist, blacklist)


class AIToolKitSites:
    async def create_or_update_site(self, name: str, config_dict: str = "{}", modules_list: List[str] = None, expires_in_seconds: int = None, **kwargs) -> str:
        """
        Creates a new sandboxed website or modifies an existing site on the userbot web server.
        
        Args:
            name: Alphanumeric unique site identifier (e.g. 'my_api', 'landing_page').
            config_dict: Security policy & isolation config JSON string or dict.
            modules_list: List of module dictionaries or JSON strings representing site python code modules.
            expires_in_seconds: Optional lifetime in seconds after which the site is deleted automatically.
        """
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        if not clean_name or clean_name != name.lower():
            return "Error: Site name must contain only alphanumeric characters, underscores, and hyphens."

        # Parse config_dict if passed as string
        if isinstance(config_dict, str):
            try:
                cfg_obj = json.loads(config_dict) if config_dict.strip() else {}
            except Exception:
                cfg_obj = {}
        else:
            cfg_obj = dict(config_dict) if config_dict else {}

        # Parse modules_list if elements are strings
        mods_obj = []
        if modules_list:
            for item in modules_list:
                if isinstance(item, str):
                    try: mods_obj.append(json.loads(item))
                    except Exception: pass
                elif isinstance(item, dict):
                    mods_obj.append(item)

        if not mods_obj and "modules" in kwargs:
            raw_m = kwargs["modules"]
            if isinstance(raw_m, list):
                mods_obj = raw_m

        allowed_imports_raw = cfg_obj.get("allowed_imports", config.SITE_ALLOWED_IMPORTS_DEFAULT)
        blocked_imports_raw = cfg_obj.get("blocked_imports", config.SITE_BLOCKED_IMPORTS_DEFAULT)
        
        allowed_imports = [imp.strip() for imp in allowed_imports_raw.split(",") if imp.strip()] if isinstance(allowed_imports_raw, str) else (allowed_imports_raw or [])
        blocked_imports = [imp.strip() for imp in blocked_imports_raw.split(",") if imp.strip()] if isinstance(blocked_imports_raw, str) else (blocked_imports_raw or [])
        
        allowed_globals = cfg_obj.get("allowed_globals", [])
        
        for glob in allowed_globals:
            if glob in ["os", "sys", "subprocess", "shutil", "builtins"]:
                return f"Security Policy Violation: Exposing global '{glob}' is strictly forbidden."
                
        from utils import matches_filter
        if isinstance(allowed_imports, list):
            for imp in allowed_imports:
                if imp not in ["all", "any", "*"] and not matches_filter(imp, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
                    return f"Security Policy Violation: Importing module '{imp}' is blocked by server sandbox policy."

        storage_limit = int(cfg_obj.get("storage_limit_bytes", config.SITE_STORAGE_LIMIT_DEFAULT))
        if storage_limit > config.SITE_STORAGE_LIMIT_MAX:
            storage_limit = config.SITE_STORAGE_LIMIT_MAX
            cfg_obj["storage_limit_bytes"] = storage_limit

        exec_timeout = float(cfg_obj.get("timeout", config.SITE_TIMEOUT_DEFAULT))
        if exec_timeout <= 0 or exec_timeout > config.SITE_TIMEOUT_MAX:
            exec_timeout = config.SITE_TIMEOUT_DEFAULT
            cfg_obj["timeout"] = exec_timeout

        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        backup_dir = config.WORKSPACE_DIR / "sites" / f"{clean_name}_backup_{int(time.time())}"
        has_backup = False
        
        if site_dir.exists():
            try:
                shutil.move(str(site_dir), str(backup_dir))
                has_backup = True
            except Exception as backup_err:
                logger.warning(f"Failed to create transactional backup for '{clean_name}': {str(backup_err)}")
                
        site_dir.mkdir(parents=True, exist_ok=True)

        if not mods_obj:
            mods_obj = [{
                "path": "index.py",
                "code": "response['body'] = '<h1>Welcome to Baziliksina dynamic site \'' + request['client_ip'] + '\'! 🌸</h1>'",
                "description": "Default home page"
            }]

        total_code_size = 0
        for mod in mods_obj:
            mod_path_str = mod.get("path", "").strip()
            resolved_mod_path = (site_dir / mod_path_str).resolve()
            if not str(resolved_mod_path).startswith(str(site_dir.resolve())):
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                return f"Security Policy Violation: Invalid module path '{mod_path_str}' attempts to escape site boundary."
                
            mod_code = mod.get("code", "")
            mod_code = mod_code.replace("\\\\r\\\\n", "\n").replace("\\\\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
            total_code_size += len(mod_code)
            
            if not matches_filter(mod_code, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                return f"Security Policy Violation: Module '{mod_path_str}' code contains terms blocked by sandbox policy."

            out_file = resolved_mod_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(mod_code)

        # Automated DevOps dry-run validation
        test_module = None
        for mod in mods_obj:
            m_path = mod.get("path", "")
            if m_path in ["index.py", "index"]:
                test_module = mod
                break
        if not test_module and mods_obj:
            test_module = mods_obj[0]
            
        if test_module:
            test_code = test_module.get("code", "")
            test_code = test_code.replace("\\\\r\\\\n", "\n").replace("\\\\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
            mock_local_vars = {
                "__import__": __import__,
                "open": lambda *a, **k: None,
                "request": {
                    "method": "GET",
                    "headers": {},
                    "query": {},
                    "body": "",
                    "json": {},
                    "client_ip": "127.0.0.1",
                    "cookies": {}
                },
                "print": lambda *a: None,
                "response": {
                    "status": 200,
                    "body": "",
                    "headers": {}
                }
            }
            try:
                import ast
                import types as py_types
                compiled_test = compile(test_code, "<test_site>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                async def run_test():
                    res = eval(compiled_test, mock_local_vars, mock_local_vars)
                    if isinstance(res, py_types.CoroutineType):
                        await res
                await asyncio.wait_for(run_test(), timeout=2.0)
                entrypoint = None
                for entry_name in ["handle", "handler", "main", "index", "get", "post"]:
                    if entry_name in mock_local_vars and callable(mock_local_vars[entry_name]):
                        entrypoint = mock_local_vars[entry_name]
                        break
                if entrypoint:
                    if inspect.iscoroutinefunction(entrypoint):
                        await entrypoint(mock_local_vars["request"])
                    else:
                        entrypoint(mock_local_vars["request"])
            except Exception as test_err:
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                import traceback
                return f"Error: Site code dry-run failed with a runtime error! Transaction rolled back to the previous stable state.\nTraceback error details:\n{traceback.format_exc()}"

        if has_backup and backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except Exception as clean_err:
                logger.warning(f"Failed to remove backup folder '{backup_dir}': {str(clean_err)}")

        total_size = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file())
        if total_size > storage_limit:
            shutil.rmtree(site_dir)
            if has_backup and backup_dir.exists():
                shutil.move(str(backup_dir), str(site_dir))
            return f"Error: Site total directory size ({total_size} bytes) exceeds the specified storage limit ({storage_limit} bytes)."

        expires_at = None
        if expires_in_seconds:
            expires_at = int(time.time()) + int(expires_in_seconds)

        await tools.db.save_dynamic_site(
            name=clean_name,
            config_dict=cfg_obj,
            modules_list=mods_obj,
            expires_at=expires_at,
            status='active'
        )

        display_host = config.WEB_SERVER_HOST
        if not display_host or display_host == "0.0.0.0":
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((config.WEB_SERVER_IP_DETECTION_HOST, config.WEB_SERVER_IP_DETECTION_PORT))
                display_host = s.getsockname()[0]
                s.close()
            except Exception:
                display_host = "127.0.0.1"

        web_link = f"http://{display_host}:{config.WEB_SERVER_PORT}/site/{clean_name}"
        expires_str = f" Expires at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))}." if expires_at else " Lifetime: Infinite."
        return (
            f"Success! Dynamic website '{clean_name}' successfully built and active!\n"
            f"- Path URL: {web_link}\n"
            f"- Local Sandbox Folder: bot_workspace/sites/{clean_name}/\n"
            f"- Configured Modules count: {len(mods_obj)}.\n"
            f"- Storage usage: {total_size} bytes (Limit: {storage_limit} bytes).\n"
            f"- API script timeout: {exec_timeout}s.{expires_str}"
        )

    async def list_sites(self, **kwargs) -> str:
        """Returns a formatted summary list of all dynamic websites currently hosted on the server."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        sites = await tools.db.get_all_dynamic_sites()
        if not sites:
            return "No active dynamic sites found."
            
        lines = ["=== Hosted Dynamic Sites List ==="]
        now = int(time.time())
        for s in sites:
            name = s["name"]
            status = s["status"]
            created_at = s["created_at"]
            expires_at = s["expires_at"]
            
            site_dir = config.WORKSPACE_DIR / "sites" / name
            size_str = "0 bytes"
            if site_dir.exists():
                size = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file())
                size_str = f"{size} bytes"
                
            expires_info = "infinite"
            if expires_at:
                remaining = expires_at - now
                if remaining <= 0:
                    expires_info = "expired (pending cleanup)"
                else:
                    expires_info = f"expires in {remaining}s"
                    
            lines.append(f"- Site: '{name}' | Status: {status} | Size: {size_str} | Lifetime: {expires_info}")
        return "\n".join(lines)

    async def get_site_details(self, name: str, **kwargs) -> str:
        """Retrieves complete metadata, security configuration, and code of all modules for the chosen site."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        site_data = await tools.db.get_dynamic_site(clean_name)
        if not site_data:
            return f"Error: Site '{clean_name}' not found."
            
        lines = [
            f"=== Dynamic Site Details for '{clean_name}' ===",
            f"- Status: {site_data['status']}",
            f"- Created At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(site_data['created_at']))}",
            f"- Expires At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(site_data['expires_at'])) if site_data['expires_at'] else 'Infinite'}"
        ]
        
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        size_bytes = 0
        if site_dir.exists():
            size_bytes = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file())
        lines.append(f"- Folder disk size: {size_bytes} bytes")
        
        try:
            cfg = json.loads(site_data["config_json"])
            lines.append("- Security Config & Policy:")
            lines.append(f"  * Allowed imports: {cfg.get('allowed_imports', [])}")
            lines.append(f"  * Allowed globals: {cfg.get('allowed_globals', [])}")
            lines.append(f"  * IP Access ACL: {cfg.get('allowed_ips', 'all')}")
            lines.append(f"  * Allowed HTTP methods: {cfg.get('allowed_methods', [])}")
            lines.append(f"  * Max request payload size: {cfg.get('max_request_size', 1024*1024)} bytes")
            lines.append(f"  * Max script execution timeout: {cfg.get('timeout', 5.0)} seconds")
            if cfg.get("custom_headers"):
                lines.append(f"  * Custom returned headers: {cfg.get('custom_headers')}")
        except Exception:
            lines.append(f"- Raw Config (JSON): {site_data['config_json']}")
            
        try:
            modules = json.loads(site_data["modules_json"])
            lines.append(f"- Registered Modules ({len(modules)}):")
            for mod in modules:
                lines.append(f"  * Path: '{mod.get('path')}' | Desc: {mod.get('description', 'none')}")
                lines.append(f"    Code:")
                lines.append("    ```python")
                code_lines = mod.get('code', '').splitlines()
                lines.append("\n".join(f"    {l}" for l in code_lines[:30]))
                if len(code_lines) > 30:
                    lines.append("    ... [code truncated]")
                lines.append("    ```")
        except Exception as e:
            lines.append(f"Error parsing modules: {str(e)}")
            
        return "\n".join(lines)

    async def get_site_logs(self, name: str, limit: int = 100, **kwargs) -> str:
        """Retrieves recent console print outputs and runtime crash tracebacks of the chosen hosted dynamic site."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        site_data = await tools.db.get_dynamic_site(clean_name)
        if not site_data:
            return f"Error: Site '{clean_name}' does not exist."
            
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        log_file = site_dir / "site.log"
        if not log_file.exists():
            return f"Info: Site '{clean_name}' log file is empty or has not been created yet."
            
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            logs = "".join(lines[-int(limit):])
            return f"=== Recent Logs for Dynamic Site '{clean_name}' ===\n{logs}"
        except Exception as e:
            return f"Error reading logs for site '{clean_name}': {str(e)}"

    async def run_site_command(self, name: str, command: str, **kwargs) -> str:
        """Executes a shell command in the context of the site isolated folder directory."""
        if not tools.db:
            return "Error: Database is not initialized."
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        if not site_dir.exists():
            return f"Error: Site '{clean_name}' does not exist on the server."
        if not check_site_command_allowed(command):
            return "Security Policy Violation: This shell command is blocked by the site execution policy."
        try:
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(site_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            res = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
            return res[:config.SANDBOX_COMMAND_CHAR_LIMIT] if len(res) > config.SANDBOX_COMMAND_CHAR_LIMIT else res if res else "Command finished with no output."
        except Exception as e:
            return f"Error executing command: {str(e)}"

    async def execute_site_python_code(self, name: str, code: str, **kwargs) -> str:
        """Executes asynchronous Python code directly inside the isolated execution environment and workspace directory of a dynamic website."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        site_data = await tools.db.get_dynamic_site(clean_name)
        if not site_data:
            return f"Error: Site '{clean_name}' does not exist on the server."
            
        from utils import matches_filter
        allowed_site_py = [i.strip() for i in config.SITE_PYTHON_WHITELIST.split(",") if i.strip()] if isinstance(config.SITE_PYTHON_WHITELIST, str) else config.SITE_PYTHON_WHITELIST
        blocked_site_py = [b.strip() for b in config.SITE_PYTHON_BLACKLIST.split(",") if b.strip()] if isinstance(config.SITE_PYTHON_BLACKLIST, str) else config.SITE_PYTHON_BLACKLIST
        
        if not matches_filter(code, allowed_site_py, blocked_site_py):
            return "Security Policy Violation: This Python code contains terms blocked by server site policy."
            
        try:
            site_config = json.loads(site_data["config_json"])
        except Exception as e:
            return f"Error reading site configuration: {str(e)}"
            
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        site_dir.mkdir(parents=True, exist_ok=True)
        
        def safe_site_open(file, mode='r', *args, **kwargs):
            if not os.path.isabs(file):
                file = site_dir / file
            resolved = Path(file).resolve()
            if not str(resolved).startswith(str(site_dir.resolve())):
                raise PermissionError("Security Policy Error: Attempted to access a directory outside the site isolated workspace.")
            return open(resolved, mode, *args, **kwargs)
            
        allowed_imports = site_config.get("allowed_imports", config.SITE_ALLOWED_IMPORTS_DEFAULT)
        blocked_imports = site_config.get("blocked_imports", config.SITE_BLOCKED_IMPORTS_DEFAULT)
        
        def safe_import(mod_name, globals=None, locals=None, fromlist=(), level=0):
            root_name = mod_name.split(".")[0]
            if not matches_filter(root_name, allowed_imports, blocked_imports):
                raise ImportError(f"Security Policy Error: Import of module '{mod_name}' is restricted for this site.")
            if not matches_filter(root_name, allowed_site_py, blocked_site_py):
                raise ImportError(f"Security Policy Error: Import of module '{mod_name}' is blocked by server policy.")
            return __import__(mod_name, globals, locals, fromlist, level)
            
        printed_lines = []
        def site_print(*args):
            line = " ".join(str(a) for a in args)
            printed_lines.append(line)
            try:
                log_file = site_dir / "site.log"
                log_line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [VM_EXEC] {line}\n"
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(log_line)
            except Exception:
                pass
                
        local_vars = {
            "__import__": safe_import,
            "open": safe_site_open,
            "print": site_print,
            "request": {
                "method": "GET",
                "headers": {},
                "query": {},
                "body": "",
                "json": {},
                "client_ip": "127.0.0.1",
                "cookies": {},
                "site_prefix": f"/site/{clean_name}",
                "base_url": f"http://127.0.0.1:{config.WEB_SERVER_PORT}/site/{clean_name}",
                "module_path": "index"
            },
            "response": {
                "status": 200,
                "body": "",
                "headers": {}
            },
            "result": None,
            "WORKSPACE_DIR": str(site_dir)
        }
        
        from utils import get_all_project_modules
        for k, v in get_all_project_modules().items():
            if k not in local_vars:
                local_vars[k] = v
                
        allowed_globals = site_config.get("allowed_globals", [])
        if "db" in allowed_globals and tools.db:
            local_vars["db"] = tools.db
        if "client" in allowed_globals and tools.client:
            from sandbox import SandboxedClient
            local_vars["client"] = SandboxedClient(tools.client, site_dir)
            
        try:
            import ast
            import types as py_types
            compiled_sandbox = compile(code, f"<site_vm_{clean_name}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            
            async def run_sandbox():
                res_val = eval(compiled_sandbox, local_vars, local_vars)
                if isinstance(res_val, py_types.CoroutineType):
                    await res_val
                    
            timeout_val = float(site_config.get("timeout", config.SITE_TIMEOUT_DEFAULT))
            await asyncio.wait_for(run_sandbox(), timeout=timeout_val)
            
            res_val = local_vars.get("result")
            resp_val = local_vars.get("response")
            
            out_parts = [f"Code executed successfully inside site '{clean_name}' isolated workspace."]
            if printed_lines:
                out_parts.append("\n=== Console Prints (stdout) ===")
                out_parts.append("\n".join(printed_lines))
            if res_val is not None:
                out_parts.append(f"\n- Variable 'result': {str(res_val)[:2000]}")
            if resp_val and resp_val.get("body"):
                out_parts.append(f"\n- Variable 'response[\"body\"]' (HTML/API Output):\n{str(resp_val['body'])[:2000]}")
                
            return "\n".join(out_parts)
        except Exception as e:
            import traceback
            return f"Error executing Python code in site '{clean_name}': {str(e)}\nTraceback details:\n{traceback.format_exc()}"

    async def delete_site(self, name: str, **kwargs) -> str:
        """Completely deletes a dynamic website, its files, and its DB records from the server."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        deleted = await tools.db.delete_dynamic_site(clean_name)
        
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        folder_removed = False
        if site_dir.exists():
            try:
                shutil.rmtree(site_dir)
                folder_removed = True
            except Exception as e:
                logger.error(f"Failed to delete site folder {site_dir}: {str(e)}")
                
        if deleted or folder_removed:
            return f"Success! Dynamic site '{clean_name}' completely deleted (Database records removed, physical files wiped)."
        return f"Error: Site '{clean_name}' does not exist on the server."


toolkit_sites = AIToolKitSites()
for attr in dir(toolkit_sites):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_sites, attr)
