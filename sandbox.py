# sandbox.py
import os
import sys
import asyncio
import logging
import inspect
import re
from pathlib import Path

# Import all project modules to pass into the isolated environment
import config
import db_manager
import key_manager
import gemini_manager
import parser
import services
import tools

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


class AsyncSandbox:
    """A universal isolated virtual machine for secure execution of asynchronous Python code."""
    def __init__(self, workspace_dir: Path, client_instance, db_instance, ai_manager_instance, chat_id=None, event=None):
        self.workspace = Path(workspace_dir).resolve()
        self.client = client_instance
        self.db = db_instance
        self.ai_manager = ai_manager_instance
        self.chat_id = chat_id
        self.event = event

    def _sandboxed_open(self, file, mode='r', *args, **kwargs):
        """Protected override of the built-in open() function."""
        if isinstance(file, str) and not os.path.isabs(file):
            file = os.path.join(str(self.workspace), file)
        
        resolved_path = os.path.abspath(file)
        if not resolved_path.startswith(str(self.workspace)):
            raise PermissionError("Security error: Attempted to access a directory outside the AI sandbox.")
        
        if any(x in resolved_path for x in ["bot.py", "config.py", "db_manager.py", "key_manager.py", "gemini_manager.py", ".env", "tools.py", "sandbox.py"]):
            raise PermissionError("Security error: Access to the bot's system files is blocked.")
            
        return open(file, mode, *args, **kwargs)

    async def execute(self, code_string: str) -> str:
        """Executes asynchronous Python code in a fully isolated context."""
        if FORBIDDEN_PYTHON_REGEX.search(code_string):
            return "Security error: This Python code is blocked by the sandbox policy."

        import asyncio
        import telethon

        # Set up the environment variables of the virtual machine (VM)
        local_vars = {
            # Proxied core objects
            "client": SandboxedClient(self.client, self.workspace),
            "db": self.db,
            "ai_manager": self.ai_manager,
            "asyncio": asyncio,
            "WORKSPACE_DIR": str(self.workspace),
            "telethon": telethon,
            "chat_id": self.chat_id,
            "event": self.event,
            "result": None,
            "open": self._sandboxed_open,
            "bot": sys.modules.get("bot"),
            "config": config,
            "db_manager": db_manager,
            "key_manager": key_manager,
            "gemini_manager": gemini_manager,
            "parser": parser,
            "services": services,
            "tools": tools
        }

        # Wrap the code in an internal asynchronous function
        indented_code = "\n".join(f"    {line}" for line in code_string.splitlines())
        wrapper_code = f"async def __run_sandbox_code():\n{indented_code}"

        try:
            exec(wrapper_code, local_vars, local_vars)
            await local_vars["__run_sandbox_code"]()
            
            res = local_vars.get("result")
            if res is None:
                return "Code executed successfully. The 'result' variable was not set."
            return f"Code executed. Result of the 'result' variable:\n{str(res)[:3000]}"
        except Exception as e:
            return f"Error executing Python code: {str(e)}"
