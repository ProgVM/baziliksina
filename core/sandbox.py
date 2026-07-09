# core/sandbox.py
import os
import sys
import asyncio
import logging
import inspect
import re
from pathlib import Path
import sys
import config
from config import SANDBOX_BLOCKED_FILES, SANDBOX_ALLOWED_FILES
from registry import registry

logger = logging.getLogger("Sandbox")

FORBIDDEN_PYTHON_REGEX = re.compile(
    r"\b(os\.system|os\.popen|subprocess|shutil\.rmtree|eval|exec)\b", 
    re.IGNORECASE
)

class SandboxedClient:
    """A secure Telethon proxy client that isolates file operations inside the sandbox."""
    def __init__(self, original_client, workspace_dir: Path):
        self._original = original_client
        self._workspace = workspace_dir

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError("Access to private attributes is blocked inside the sandbox.")
        attr = getattr(self._original, name)
        if callable(attr):
            if name in ["download_media", "download_profile_photo", "upload_file", "send_file"]:
                return self._wrap_file_method(attr, name)
        return attr

    def _wrap_file_method(self, method, method_name):
        async def wrapper(*args, **kwargs):
            if method_name in ["download_media", "download_profile_photo"]:
                has_file_arg = len(args) > 1 or "file" in kwargs
                if not has_file_arg:
                    kwargs["file"] = str(self._workspace)

            new_args = []
            for arg in args:
                if isinstance(arg, str) and not os.path.isabs(arg) and "." in arg:
                    new_args.append(str(self._workspace / arg))
                else:
                    new_args.append(arg)
            
            for k, v in list(kwargs.items()):
                if k in ["file", "photo", "document", "video", "voice", "audio"] and isinstance(v, str):
                    if not os.path.isabs(v):
                        kwargs[k] = str(self._workspace / v)
            
            result = method(*new_args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        return wrapper

    def __call__(self, *args, **kwargs):
        return self._original(*args, **kwargs)


class SandboxedConfig:
    """Secure proxy for config module that hides sensitive API keys and hashes from sandboxed code."""
    def __init__(self, original_config):
        self._original = original_config

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError("Access to private attributes is blocked inside the sandbox.")
        from utils import matches_filter
        import config
        whitelist = getattr(config, "SANDBOX_CONFIG_WHITELIST", [])
        blacklist = getattr(config, "SANDBOX_CONFIG_BLACKLIST", [])
        if not matches_filter(name, whitelist, blacklist, default_allow=True):
            return "[REDACTED_SECURITY_SENSITIVE_DATA]"
        attr = getattr(self._original, name)
        return attr


class AsyncSandbox:
    """A universal isolated virtual machine for secure execution of asynchronous Python code."""
    def __init__(self, workspace_dir: Path, client_instance, db_instance, ai_manager_instance, chat_id=None, event=None):
        self.workspace = Path(workspace_dir).resolve()
        self.client = client_instance
        self.db = db_instance
        self.ai_manager = ai_manager_instance
        self.chat_id = chat_id
        self.event = event
        self.me = None

    def _sandboxed_open(self, file, mode='r', *args, **kwargs):
        """Protected override of the built-in open() function."""
        if isinstance(file, str) and not os.path.isabs(file):
            file = os.path.join(str(self.workspace), file)
        
        resolved_path = Path(file).resolve()
        if not str(resolved_path).startswith(str(self.workspace)):
            raise PermissionError("Security error: Attempted to access a directory outside the AI sandbox.")
        
        filename = os.path.basename(resolved_path)
        from utils import matches_filter

        # Transform comma-separated configuration string parameters into structured lists securely
        allowed_list = [f.strip() for f in SANDBOX_ALLOWED_FILES.split(",") if f.strip()] if isinstance(SANDBOX_ALLOWED_FILES, str) else SANDBOX_ALLOWED_FILES
        blocked_list = [f.strip() for f in SANDBOX_BLOCKED_FILES.split(",") if f.strip()] if isinstance(SANDBOX_BLOCKED_FILES, str) else SANDBOX_BLOCKED_FILES

        if not matches_filter(filename, allowed_list, blocked_list):
            raise PermissionError("Security error: Access to this file is blocked by sandbox policy.")
            
        return open(file, mode, *args, **kwargs)

    async def execute(self, code_string: str) -> str:
        """Executes asynchronous Python code in a fully isolated context."""
        if FORBIDDEN_PYTHON_REGEX.search(code_string):
            return "Security error: This Python code is blocked by the sandbox policy."

        import asyncio
        import telethon
        
        # Resolve own profile dynamically for the VM context
        if self.me is None and self.client:
            try:
                self.me = await self.client.get_me()
            except Exception:
                pass

        # Set up the secure environment variables of the virtual machine (VM)
        local_vars = {
            "client": SandboxedClient(self.client, self.workspace),
            "db": self.db,
            "ai_manager": self.ai_manager,
            "registry": registry,
            "asyncio": asyncio,
            "WORKSPACE_DIR": str(self.workspace),
            "telethon": telethon,
            "chat_id": self.chat_id,
            "event": self.event,
            "me": self.me,
            "result": None,
            "open": self._sandboxed_open,
            "config": SandboxedConfig(config),
        }

        # Dynamically inject all project modules to keep sandbox dependencies fully synchronized
        from utils import get_all_project_modules
        for k, v in get_all_project_modules().items():
            if k not in local_vars:
                local_vars[k] = v

        # Dynamically inject all registered system and custom tools directly as VM functions
        for tool in registry.get_all_tools():
            local_vars[tool.name] = tool.callable

        try:
            import ast
            import types
            # Compile code directly as a module with top-level await support (resolves local scope trap)
            compiled_sandbox = compile(code_string, "<sandbox_vm>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            
            res_val = eval(compiled_sandbox, local_vars, local_vars)
            if isinstance(res_val, types.CoroutineType):
                await res_val
            
            res = local_vars.get("result")
            if res is None:
                return "Code executed successfully. The 'result' variable was not set."
            return f"Code executed. Result of the 'result' variable:\n{str(res)[:3000]}"
        except Exception as e:
            # VM state self-cleaning upon crash
            local_vars.clear()
            return f"Error executing Python code: {str(e)}"