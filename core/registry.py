# core/registry.py
import os
import json
import asyncio
import logging
import inspect
import urllib.parse
from pathlib import Path
import httpx
from google.genai import types

logger = logging.getLogger("Registry")


class ToolMetadata:
    """Metadata class for storing complete information about a registered tool."""
    def __init__(self, name: str, callable_func: callable, category: str, description: str = None, is_custom: bool = False, parameters_schema: dict = None):
        self.name = name
        self.callable = callable_func
        self.category = category
        self.description = description or getattr(callable_func, "__doc__", "") or "Description is missing."
        self.is_custom = is_custom
        self.parameters_schema = parameters_schema


class FunctionRegistry:
    """Thread-safe singleton registry of all available AI tools (system and custom)."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._registry = {} # {tool_name_str: ToolMetadata}
        return cls._instance

    def register(self, name: str, callable_func: callable, category: str, description: str = None, is_custom: bool = False, parameters_schema: dict = None):
        """Registers a new tool in the catalog."""
        self._registry[name] = ToolMetadata(
            name=name,
            callable_func=callable_func,
            category=category,
            description=description,
            is_custom=is_custom,
            parameters_schema=parameters_schema
        )
        logger.debug(f"Tool '{name}' [{'custom' if is_custom else 'system'}] successfully registered.")

    def unregister(self, name: str) -> bool:
        """Removes a tool from the registry by its name. Returns True if the removal is successful."""
        if name in self._registry:
            del self._registry[name]
            logger.debug(f"Tool '{name}' successfully removed from the registry.")
            return True
        return False

    def get(self, name: str) -> ToolMetadata:
        """Returns tool metadata by its name."""
        return self._registry.get(name)

    def get_all_tools(self) -> list:
        """Returns a list of all registered tools."""
        return list(self._registry.values())

    def get_all_callables(self) -> list:
        """Returns a flat list of all callable function objects to be passed to the Gemini API."""
        return [tool.callable for tool in self._registry.values()]

    def get_by_category(self, category: str) -> list:
        """Filters and returns tools belonging to a specific category."""
        return [tool for tool in self._registry.values() if tool.category == category]

    def clear_custom_tools(self):
        """Removes all custom tools from the registry's RAM."""
        custom_names = [name for name, tool in self._registry.items() if tool.is_custom]
        for name in custom_names:
            del self._registry[name]
        logger.info(f"Cleared custom tools from the active registry: {len(custom_names)}")


class TagBlockMetadata:
    """Metadata class for storing complete information about registered tags, labels, and blocks."""
    def __init__(self, name: str, type_str: str, callable_func: callable, description: str = None, is_custom: bool = False, code: str = None):
        self.name = name
        self.type = type_str  # 'tag' or 'block'
        self.callable = callable_func
        self.description = description or getattr(callable_func, "__doc__", "") or "No description."
        self.is_custom = is_custom
        self.code = code

class TagBlockRegistry:
    """Thread-safe singleton registry of all available AI system/custom tags and blocks."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._registry = {} # {name_str: TagBlockMetadata}
        return cls._instance

    def register(self, name: str, type_str: str, callable_func: callable, description: str = None, is_custom: bool = False, code: str = None):
        self._registry[name] = TagBlockMetadata(name, type_str, callable_func, description, is_custom, code)
        logger.debug(f"Tag/Block '{name}' [{'custom' if is_custom else 'system'}] successfully registered.")

    def unregister(self, name: str) -> bool:
        if name in self._registry:
            del self._registry[name]
            return True
        return False

    def get(self, name: str) -> TagBlockMetadata:
        return self._registry.get(name)

    def get_all(self) -> list:
        return list(self._registry.values())

    def clear_custom(self):
        custom_names = [name for name, item in self._registry.items() if item.is_custom]
        for name in custom_names:
            del self._registry[name]

tag_block_registry = TagBlockRegistry()

# Global registry singleton object
registry = FunctionRegistry()


def compile_custom_tool(name: str, code_str: str, namespace: dict = None) -> callable:
    """
    Compiles Python code of a custom function/command from a text string with top-level await support
    and returns an asynchronous execution wrapper with auto-injected CLI argument aliases.
    """
    import tools
    import ast
    import inspect
    import types as py_types

    if namespace is None:
        namespace = {
            "client": tools.client,
            "db": tools.db,
            "ai_manager": tools.ai_manager,
            "permission_manager": getattr(tools, "permission_manager", None),
            "service_manager": getattr(tools, "service_manager", None),
            "command_manager": getattr(tools, "command_manager", None),
            "logger": logging.getLogger(f"CustomTool.{name}"),
            "httpx": httpx,
            "json": json,
            "asyncio": asyncio,
            "Path": Path,
            "urllib": urllib,
            "types": types,
            "os": os,
            "result": None
        }

    from utils import get_all_project_modules
    for k, v in get_all_project_modules().items():
        if k not in namespace:
            namespace[k] = v

    for tool in registry.get_all_tools():
        if tool.name not in namespace:
            namespace[tool.name] = tool.callable

    try:
        compiled_code = compile(code_str, f"<custom_{name}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as se:
        if "return" in str(se):
            indented = "\n".join("    " + line for line in code_str.splitlines())
            wrapped_code = f"async def {name}(*args, **kwargs):\n{indented}"
            compiled_code = compile(wrapped_code, f"<custom_{name}>", "exec")
        else:
            raise

    async def _execution_wrapper(*args, **kwargs):
        namespace["args"] = args
        namespace["kwargs"] = kwargs
        if kwargs:
            for k, v in kwargs.items():
                namespace[k] = v

        cli_args_obj = kwargs.get("cli_args")
        if cli_args_obj:
            raw_tail = getattr(cli_args_obj, "raw_tail", "")
            namespace["payload"] = raw_tail
            namespace["text"] = raw_tail
            namespace["args_str"] = raw_tail
            namespace["positional"] = getattr(cli_args_obj, "positional", [])
            namespace["flags"] = getattr(cli_args_obj, "flags", {})

        coro_or_val = eval(compiled_code, namespace, namespace)
        if isinstance(coro_or_val, py_types.CoroutineType):
            await coro_or_val

        func = namespace.get(name)
        if func and callable(func) and func != _execution_wrapper:
            sig = inspect.signature(func)
            
            system_names = {
                "client", "db", "ai_manager", "permission_manager", "service_manager",
                "command_manager", "logger", "httpx", "json", "asyncio", "Path",
                "urllib", "types", "os", "cli_args", "event", "user_id", "chat_id"
            }

            available_args = {}
            available_args.update(namespace)
            available_args.update(kwargs)

            positional_items = []
            if cli_args_obj and hasattr(cli_args_obj, "positional") and cli_args_obj.positional:
                positional_items = list(cli_args_obj.positional)
            elif args:
                positional_items = list(args)

            cli_flags = getattr(cli_args_obj, "flags", {}) if cli_args_obj else {}

            bound_args = {}
            pos_args = []

            for p_name, p_param in sig.parameters.items():
                if p_param.kind == inspect.Parameter.VAR_POSITIONAL:
                    pos_args.extend(positional_items)
                    positional_items.clear()
                elif p_param.kind == inspect.Parameter.VAR_KEYWORD:
                    for k, v in available_args.items():
                        if k not in bound_args:
                            bound_args[k] = v
                elif p_param.kind == inspect.Parameter.POSITIONAL_ONLY:
                    if positional_items:
                        pos_args.append(positional_items.pop(0))
                    elif p_name in available_args:
                        pos_args.append(available_args[p_name])
                else:
                    if p_name in system_names and p_name in available_args:
                        bound_args[p_name] = available_args[p_name]
                    elif p_name in cli_flags:
                        bound_args[p_name] = cli_flags[p_name]
                    elif positional_items:
                        bound_args[p_name] = positional_items.pop(0)
                    elif p_name in available_args:
                        bound_args[p_name] = available_args[p_name]

            if inspect.iscoroutinefunction(func):
                return await func(*pos_args, **bound_args)
            else:
                return func(*pos_args, **bound_args)

        return namespace.get("result")

    return _execution_wrapper


async def sync_custom_tools_with_db(db_manager):
    """
    Asynchronously reads all custom tools from the SQLite database,
    compiles their code on the fly, and registers them in the active FunctionRegistry.
    """
    logger.info("Starting synchronization of custom tools with the database...")
    
    # First, clear old custom tools to avoid duplicates during recompilation
    registry.clear_custom_tools()
    
    try:
        custom_tools_list = await db_manager.get_all_custom_tools()
        success_count = 0
        
        for tool_data in custom_tools_list:
            try:
                name = tool_data["name"]
                category = tool_data["category"]
                desc = tool_data["description"]
                code = tool_data["code"]
                
                # Compile the function code from the string
                compiled_func = compile_custom_tool(name, code)
                
                # Register in the global singleton
                registry.register(
                    name=name,
                    callable_func=compiled_func,
                    category=category,
                    description=desc,
                    is_custom=True
                )
                success_count += 1
            except Exception as err:
                logger.error(f"Failed to compile and register custom tool '{tool_data.get('name')}': {str(err)}")

        logger.info(f"Synchronization complete. Successfully compiled and added tools: {success_count}/{len(custom_tools_list)}")
    except Exception as db_err:
        logger.error(f"Error reading custom tools from the SQLite database: {str(db_err)}")

async def sync_custom_tags_blocks_with_db(db_manager):
    """
    Asynchronously reads all custom tags and blocks from the SQLite database,
    compiles their code, and registers them in the active TagBlockRegistry at startup.
    """
    logger.info("Starting synchronization of custom tags and blocks with the database...")
    try:
        custom_tb_list = await db_manager.get_all_custom_tags_blocks()
        success_count = 0
        for item in custom_tb_list:
            try:
                name = item["name"]
                type_str = item["type"]
                desc = item["description"]
                code = item["code"]
                compiled_func = compile_custom_tool(name, code)
                tag_block_registry.register(
                    name=name,
                    type_str=type_str,
                    callable_func=compiled_func,
                    description=desc,
                    is_custom=True,
                    code=code
                )
                success_count += 1
            except Exception as err:
                logger.error(f"Failed to compile custom tag/block '{item.get('name')}': {str(err)}")
        logger.info(f"Custom tags and blocks synchronization complete. Loaded: {success_count}/{len(custom_tb_list)}")
    except Exception as db_err:
        logger.error(f"Error reading custom tags/blocks from SQLite: {str(db_err)}")
