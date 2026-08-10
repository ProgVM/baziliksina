# tools/service_tools.py
import os
import json
import logging
from typing import List, Dict, Any, Optional

import config
import tools
from permission_manager import permission_manager
from service_manager import service_manager

logger = logging.getLogger("Tools.Services")

class AIToolKitServices:
    async def create_or_update_custom_service(self, name: str, code: str, description: str = None, status: str = "stopped", **kwargs) -> str:
        """
        Creates a new or updates an existing background service.

        Args:
            name: Unique service identifier.
            code: Asynchronous Python code running in a background task loop.
            description: Short description of the service's background task.
            status: Initial status ('running' or 'stopped').
        """
        if not permission_manager.can_ai_perform("SERVICES", "CREATE") and not permission_manager.can_ai_perform("SERVICES", "EDIT"):
            return "Error: Permission denied. AI cannot create/edit services."

        if not tools.db:
            return "Error: Database is not initialized."

        from utils import matches_filter
        if not matches_filter(code, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
            return "Security error: Service Python code contains blocked terms."

        try:
            await tools.db.save_custom_service(name, code, description=description, status=status)
            service_manager.register_service(name, code, description=description or "Custom Service", is_custom=True, status=status)
            if status == "running":
                await service_manager.start_service(name)
            return f"Success! Custom service '{name}' created/updated."
        except Exception as e:
            return f"Error saving custom service: {str(e)}"

    async def delete_custom_service(self, name: str, **kwargs) -> str:
        """Deletes a custom background service."""
        if not permission_manager.can_ai_perform("SERVICES", "DELETE"):
            return "Error: Permission denied."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            await service_manager.stop_service(name)
            deleted = await tools.db.delete_custom_service(name)
            return f"Success. Service '{name}' deleted." if deleted else f"Error: Service '{name}' not found."
        except Exception as e:
            return f"Error deleting custom service: {str(e)}"

    async def start_service(self, name: str, **kwargs) -> str:
        """Starts a registered background service by name."""
        if not permission_manager.can_ai_perform("SERVICES", "INVOKE"):
            return "Error: Permission denied."

        ok = await service_manager.start_service(name)
        return f"Service '{name}' started." if ok else f"Error starting service '{name}'."

    async def stop_service(self, name: str, **kwargs) -> str:
        """Stops a running background service by name."""
        if not permission_manager.can_ai_perform("SERVICES", "INVOKE"):
            return "Error: Permission denied."

        ok = await service_manager.stop_service(name)
        return f"Service '{name}' stopped." if ok else f"Error stopping service '{name}'."

    async def list_services(self, **kwargs) -> str:
        """Returns a formatted list of all registered background services and their statuses."""
        if not permission_manager.can_ai_perform("SERVICES", "LIST"):
            return "Error: Permission denied."

        services = service_manager.list_services()
        if not services:
            return "No registered services found."
        lines = [f"- {s['name']} [{s['status']}] {'(Custom)' if s['is_custom'] else '(System)'}: {s['description'] or 'No description'}" for s in services]
        return f"=== Registered Services ({len(services)}) ===\n" + "\n".join(lines)

    # --- CRON JOBS TOOLS ---
    async def create_or_update_custom_cron_job(self, name: str, schedule_spec: str, code: str, description: str = None, status: str = "active", **kwargs) -> str:
        """
        Creates a new or updates an existing recurring cron job.

        Args:
            name: Unique cron job identifier.
            schedule_spec: Interval spec (e.g. '60' for 60s, '300', 'every 5m').
            code: Python code executed at each cron interval.
            description: Short description of the cron job's task.
            status: Initial status ('active' or 'stopped').
        """
        if not permission_manager.can_ai_perform("CRON", "CREATE") and not permission_manager.can_ai_perform("CRON", "EDIT"):
            return "Error: Permission denied."

        if not tools.db:
            return "Error: Database is not initialized."

        from utils import matches_filter
        if not matches_filter(code, config.SANDBOX_PYTHON_WHITELIST, config.SANDBOX_PYTHON_BLACKLIST):
            return "Security error: Cron job Python code contains blocked terms."

        try:
            await tools.db.save_custom_cron_job(name, schedule_spec, code, description=description, status=status)
            service_manager.register_cron_job(name, schedule_spec, code, description=description or "Custom Cron Job", is_custom=True, status=status)
            if status == "active":
                await service_manager.start_cron_job(name)
            return f"Success! Custom cron job '{name}' created/updated."
        except Exception as e:
            return f"Error saving custom cron job: {str(e)}"

    async def delete_custom_cron_job(self, name: str, **kwargs) -> str:
        """Deletes a custom cron job."""
        if not permission_manager.can_ai_perform("CRON", "DELETE"):
            return "Error: Permission denied."

        if not tools.db:
            return "Error: Database is not initialized."

        try:
            await service_manager.stop_cron_job(name)
            deleted = await tools.db.delete_custom_cron_job(name)
            return f"Success. Cron job '{name}' deleted." if deleted else f"Error: Cron job '{name}' not found."
        except Exception as e:
            return f"Error deleting custom cron job: {str(e)}"

    async def start_cron_job(self, name: str, **kwargs) -> str:
        """Starts a registered cron job ticker."""
        if not permission_manager.can_ai_perform("CRON", "INVOKE"):
            return "Error: Permission denied."

        ok = await service_manager.start_cron_job(name)
        return f"Cron job '{name}' started." if ok else f"Error starting cron job '{name}'."

    async def stop_cron_job(self, name: str, **kwargs) -> str:
        """Stops a running cron job ticker."""
        if not permission_manager.can_ai_perform("CRON", "INVOKE"):
            return "Error: Permission denied."

        ok = await service_manager.stop_cron_job(name)
        return f"Cron job '{name}' stopped." if ok else f"Error stopping cron job '{name}'."

    async def list_cron_jobs(self, **kwargs) -> str:
        """Returns a formatted list of all registered cron jobs and their statuses."""
        if not permission_manager.can_ai_perform("CRON", "LIST"):
            return "Error: Permission denied."

        cron_jobs = service_manager.list_cron_jobs()
        if not cron_jobs:
            return "No registered cron jobs found."
        lines = [f"- {c['name']} [{c['schedule_spec']}] - Status: {c['status']} {'(Custom)' if c['is_custom'] else '(System)'}" for c in cron_jobs]
        return f"=== Registered Cron Jobs ({len(cron_jobs)}) ===\n" + "\n".join(lines)


toolkit_services = AIToolKitServices()
for attr in dir(toolkit_services):
    if not attr.startswith("_"):
        globals()[attr] = getattr(toolkit_services, attr)
