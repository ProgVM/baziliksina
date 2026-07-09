# tools/site_tools.py
import os
import json
import logging
import time
import shutil
import asyncio
from pathlib import Path
from typing import List, Dict, Any

import config
import tools
from utils import matches_filter

logger = logging.getLogger("Tools.Sites")

# Default values are retrieved dynamically from the central config module

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
    async def create_or_update_site(self, name: str, config_dict: Dict[str, Any], modules_list: List[Dict[str, Any]] = None, expires_in_seconds: int = None, **kwargs) -> str:
        """
        Creates a new sandboxed website or modifies an existing site on the userbot web server.
        
        Args:
            name: Alphanumeric unique site identifier (e.g. 'my_api', 'landing_page').
            config_dict: Security policy & isolation config. Keys:
                - allowed_imports: List[str] of libraries site code can import (e.g. ['json', 'math']).
                - allowed_globals: List[str] of variables exposed from host ('db' or 'client').
                - allowed_ips: Comma-separated list of allowed client IPs or 'all'.
                - allowed_methods: List[str] of allowed HTTP verbs (e.g. ['GET', 'POST']).
                - max_request_size: Int limit in bytes (default 1MB).
                - storage_limit_bytes: Int limit of dynamic folder size.
                - timeout: Float execution script timeout in seconds.
                - custom_headers: Dict[str, str] headers returned in every response.
            modules_list: List of dicts representing site python code modules. Each dict must have:
                - path: relative filename (e.g., 'index.py', 'api/user.py').
                - code: python script content.
                - description: description of the module.
            expires_in_seconds: Optional lifetime in seconds after which the site is deleted automatically.
        """
        if not tools.db:
            return "Error: Database is not initialized."
            
        # Clean site name to prevent path traversal
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        if not clean_name or clean_name != name.lower():
            return "Error: Site name must contain only alphanumeric characters, underscores, and hyphens."

        # Validate security limits and values
        allowed_imports_raw = config_dict.get("allowed_imports", config.SITE_ALLOWED_IMPORTS_DEFAULT)
        blocked_imports_raw = config_dict.get("blocked_imports", config.SITE_BLOCKED_IMPORTS_DEFAULT)
        
        allowed_imports = [imp.strip() for imp in allowed_imports_raw.split(",") if imp.strip()] if isinstance(allowed_imports_raw, str) else (allowed_imports_raw or [])
        blocked_imports = [imp.strip() for imp in blocked_imports_raw.split(",") if imp.strip()] if isinstance(blocked_imports_raw, str) else (blocked_imports_raw or [])
        
        allowed_globals = config_dict.get("allowed_globals", [])
        
        # Enforce blacklist of dangerous global models and imports on custom site engines
        for glob in allowed_globals:
            if glob in ["os", "sys", "subprocess", "shutil", "builtins"]:
                return f"Security Policy Violation: Exposing global '{glob}' is strictly forbidden."
                
        # Validate whitelisted imports against server absolute sandbox policy
        from utils import matches_filter
        if isinstance(allowed_imports, list):
            for imp in allowed_imports:
                if imp not in ["all", "any", "*"] and not matches_filter(imp, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
                    return f"Security Policy Violation: Importing module '{imp}' is blocked by server sandbox policy."

        # Enforce storage limits
        storage_limit = int(config_dict.get("storage_limit_bytes", config.SITE_STORAGE_LIMIT_DEFAULT))
        if storage_limit > config.SITE_STORAGE_LIMIT_MAX:
            storage_limit = config.SITE_STORAGE_LIMIT_MAX
            config_dict["storage_limit_bytes"] = storage_limit

        # Enforce script timeout boundaries
        exec_timeout = float(config_dict.get("timeout", config.SITE_TIMEOUT_DEFAULT))
        if exec_timeout <= 0 or exec_timeout > config.SITE_TIMEOUT_MAX:
            exec_timeout = config.SITE_TIMEOUT_DEFAULT
            config_dict["timeout"] = exec_timeout

        # Setup safe transactional backup to support non-destructive updates
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

        if not modules_list:
            # Create a default home page if no modules are specified
            modules_list = [{
                "path": "index.py",
                "code": "response['body'] = '<h1>Welcome to Baziliksina dynamic site \'' + request['client_ip'] + '\'! 🌸</h1>'",
                "description": "Default home page"
            }]

        # Write each module file to the site isolated directory after verifying it
        total_code_size = 0
        for mod in modules_list:
            mod_path_str = mod.get("path", "").strip()
            # Clean and prevent directory traversal
            clean_mod_path = Path(mod_path_str).relative_to(Path(mod_path_str).anchor)
            if ".." in str(clean_mod_path) or clean_mod_path.is_absolute():
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                return f"Security Policy Violation: Invalid module path '{mod_path_str}'."
                
            mod_code = mod.get("code", "")
            # Safe normalization of double and single escaped newlines
            mod_code = mod_code.replace("\\\\r\\\\n", "\n").replace("\\\\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
            total_code_size += len(mod_code)
            
            # Verify Python code of module
            if not matches_filter(mod_code, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                return f"Security Policy Violation: Module '{mod_path_str}' code contains terms blocked by sandbox policy."

            # Save file physically to sandbox
            out_file = site_dir / clean_mod_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(mod_code)

        # --- AUTOMATED DEVOPS DRY-RUN VALIDATION ---
        test_module = None
        for mod in modules_list:
            m_path = mod.get("path", "")
            if m_path in ["index.py", "index"]:
                test_module = mod
                break
        if not test_module and modules_list:
            test_module = modules_list[0]
            
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
                indented_test = "\n".join(f"    {line}" for line in test_code.splitlines())
                wrapper_test = f"async def __run_module():\n{indented_test}"
                exec(wrapper_test, mock_local_vars, mock_local_vars)
                await asyncio.wait_for(mock_local_vars["__run_module"](), timeout=2.0)
                
                # Dry-run execution of entrypoint handlers inside site validation step
                entrypoint = None
                for name in ["handle", "handler", "main", "index", "get", "post"]:
                    if name in mock_local_vars and callable(mock_local_vars[name]):
                        entrypoint = mock_local_vars[name]
                        break
                if entrypoint:
                    import inspect
                    if inspect.iscoroutinefunction(entrypoint):
                        await entrypoint(mock_local_vars["request"])
                    else:
                        entrypoint(mock_local_vars["request"])
            except Exception as test_err:
                # Clean up the broken files
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                # Rollback: Restore previous stable site directory if backup exists
                if has_backup and backup_dir.exists():
                    shutil.move(str(backup_dir), str(site_dir))
                import traceback
                return f"Error: Site code dry-run failed with a runtime error! Transaction rolled back to the previous stable state.\nTraceback error details:\n{traceback.format_exc()}"

        # Clean up backup directory upon successful validation
        if has_backup and backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except Exception as clean_err:
                logger.warning(f"Failed to remove backup folder '{backup_dir}': {str(clean_err)}")

        # Apply disk limits check
        # Calculate size of site directory
        total_size = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file())
        if total_size > storage_limit:
            # Cleanup and revert
            shutil.rmtree(site_dir)
            if has_backup and backup_dir.exists():
                shutil.move(str(backup_dir), str(site_dir))
            return f"Error: Site total directory size ({total_size} bytes) exceeds the specified storage limit ({storage_limit} bytes)."

        # Determine expiration
        expires_at = None
        if expires_in_seconds:
            expires_at = int(time.time()) + int(expires_in_seconds)

        # Save site schema to DB
        await tools.db.save_dynamic_site(
            name=clean_name,
            config_dict=config_dict,
            modules_list=modules_list,
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
            f"- Configured Modules count: {len(modules_list)}.\n"
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
            
            # Calculate size
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
        
        # Read files and actual disk size
        site_dir = config.WORKSPACE_DIR / "sites" / clean_name
        size_bytes = 0
        if site_dir.exists():
            size_bytes = sum(f.stat().st_size for f in site_dir.glob('**/*') if f.is_file())
        lines.append(f"- Folder disk size: {size_bytes} bytes")
        
        # Read configuration parameters nicely
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
            
        # Display modules code
        try:
            modules = json.loads(site_data["modules_json"])
            lines.append(f"- Registered Modules ({len(modules)}):")
            for mod in modules:
                lines.append(f"  * Path: '{mod.get('path')}' | Desc: {mod.get('description', 'none')}")
                lines.append(f"    Code:")
                lines.append("    ```python")
                code_lines = mod.get('code', '').splitlines()
                # Print first 30 lines of module to avoid output truncation
                lines.append("\n".join(f"    {l}" for l in code_lines[:30]))
                if len(code_lines) > 30:
                    lines.append("    ... [code truncated]")
                lines.append("    ```")
        except Exception as e:
            lines.append(f"Error parsing modules: {str(e)}")
            
        return "\n".join(lines)

    async def get_site_logs(self, name: str, limit: int = 100, **kwargs) -> str:
        """
        Retrieves recent console print outputs and runtime crash tracebacks of the chosen hosted dynamic site.
        
        Args:
            name: The site name/id (e.g. 'my_api').
            limit: Number of recent lines to read. Default is 100.
        """
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
        """
        Executes a shell command in the context of the site isolated folder directory.
        
        Args:
            name: The dynamic site identifier.
            command: The shell command to run (e.g., 'pip install colored' or 'ls -la').
        """
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

    async def delete_site(self, name: str, **kwargs) -> str:
        """Completely deletes a dynamic website, its files, and its DB records from the server."""
        if not tools.db:
            return "Error: Database is not initialized."
            
        clean_name = "".join(c for c in name if c.isalnum() or c in ["_", "-"]).strip().lower()
        deleted = await tools.db.delete_dynamic_site(clean_name)
        
        # Physically remove directory from the workspace safely
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

# Export toolkit methods
toolkit_sites = AIToolKitSites()
for attr in dir(toolkit_sites):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_sites, attr)
