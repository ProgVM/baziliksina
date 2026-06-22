# registry.py
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


# Global registry singleton object
registry = FunctionRegistry()


def compile_custom_tool(name: str, code_str: str, namespace: dict = None) -> callable:
    """
    Compiles Python code of a custom function from a text string and returns its callable object.
    Provides the custom function with safe, isolated access to the bot core.
    """
    import tools # Import tools locally to avoid circular imports

    if namespace is None:
        # Provide the custom code with direct access to all proxy objects and system libraries
        namespace = {
            "client": tools.client,
            "db": tools.db,
            "ai_manager": tools.ai_manager,
            "logger": logging.getLogger(f"CustomTool.{name}"),
            "httpx": httpx,
            "json": json,
            "asyncio": asyncio,
            "Path": Path,
            "urllib": urllib,
            "types": types,
            "os": os
        }
    
    # Safely compile and execute the function code within the namespace
    exec(code_str, namespace, namespace)
    func = namespace.get(name)
    
    if not func or not callable(func):
        raise AttributeError(f"Custom tool code must contain a function named '{name}'!")
        
    return func


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
