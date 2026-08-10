# core/service_manager.py
import os
import sys
import json
import time
import asyncio
import logging
import inspect
from typing import Dict, Any, Optional, List, Callable, Union

import config
from registry import compile_custom_tool

logger = logging.getLogger("ServiceManager")


class ServiceMetadata:
    """Stores runtime state and metadata for background services."""
    def __init__(self, name: str, callable_or_code: Union[Callable, str], description: str = "", is_custom: bool = False, status: str = "stopped"):
        self.name = name
        self.callable_or_code = callable_or_code
        self.description = description
        self.is_custom = is_custom
        self.status = status
        self.task: Optional[asyncio.Task] = None


class CronJobMetadata:
    """Stores runtime state and metadata for periodic cron tasks."""
    def __init__(self, name: str, schedule_spec: str, callable_or_code: Union[Callable, str], description: str = "", is_custom: bool = False, status: str = "active"):
        self.name = name
        self.schedule_spec = schedule_spec
        self.callable_or_code = callable_or_code
        self.description = description
        self.is_custom = is_custom
        self.status = status
        self.last_run: Optional[int] = None
        self.task: Optional[asyncio.Task] = None


class ServiceManager:
    """
    Unified manager orchestrating long-running background services, 
    periodic cron jobs, database persistence, and lifecycle controls.
    """
    def __init__(self, db_manager=None, client_instance=None, ai_manager_instance=None):
        self.db = db_manager
        self.client = client_instance
        self.ai_manager = ai_manager_instance
        self._services: Dict[str, ServiceMetadata] = {}
        self._cron_jobs: Dict[str, CronJobMetadata] = {}

    def bind_core_references(self, db_manager, client_instance, ai_manager_instance):
        """Binds core references required for execution context."""
        self.db = db_manager
        self.client = client_instance
        self.ai_manager = ai_manager_instance

    # --- SERVICE MANAGEMENT ---
    def register_service(self, name: str, callable_or_code: Union[Callable, str], description: str = "", is_custom: bool = False, status: str = "stopped"):
        """Registers a background service in memory."""
        self._services[name] = ServiceMetadata(
            name=name,
            callable_or_code=callable_or_code,
            description=description,
            is_custom=is_custom,
            status=status
        )
        logger.debug(f"Service '{name}' [{'custom' if is_custom else 'system'}] registered.")

    async def start_service(self, name: str) -> bool:
        """Starts a background service by name."""
        srv = self._services.get(name)
        if not srv:
            logger.warning(f"Service '{name}' not found.")
            return False

        if srv.task and not srv.task.done():
            logger.info(f"Service '{name}' is already running.")
            return True

        async def _run_wrapper():
            srv.status = "running"
            try:
                if callable(srv.callable_or_code):
                    if inspect.iscoroutinefunction(srv.callable_or_code):
                        await srv.callable_or_code()
                    else:
                        res = srv.callable_or_code()
                        if inspect.isawaitable(res):
                            await res
                elif isinstance(srv.callable_or_code, str):
                    compiled_func = compile_custom_tool(srv.name, srv.callable_or_code)
                    if inspect.iscoroutinefunction(compiled_func):
                        await compiled_func()
                    else:
                        res = compiled_func()
                        if inspect.isawaitable(res):
                            await res
            except asyncio.CancelledError:
                logger.info(f"Service '{name}' task cancelled.")
            except Exception as e:
                logger.error(f"Error in background service '{name}': {str(e)}")
            finally:
                srv.status = "stopped"

        srv.task = asyncio.create_task(_run_wrapper())
        srv.status = "running"

        if self.db and srv.is_custom:
            await self.db.save_custom_service(srv.name, str(srv.callable_or_code), srv.description, status="running")

        logger.info(f"Service '{name}' successfully started.")
        return True

    async def stop_service(self, name: str) -> bool:
        """Stops a running background service by name."""
        srv = self._services.get(name)
        if not srv:
            return False

        if srv.task and not srv.task.done():
            srv.task.cancel()
            try:
                await srv.task
            except asyncio.CancelledError:
                pass

        srv.status = "stopped"
        if self.db and srv.is_custom:
            await self.db.save_custom_service(srv.name, str(srv.callable_or_code), srv.description, status="stopped")

        logger.info(f"Service '{name}' stopped.")
        return True

    # --- CRON MANAGEMENT ---
    def register_cron_job(self, name: str, schedule_spec: str, callable_or_code: Union[Callable, str], description: str = "", is_custom: bool = False, status: str = "active"):
        """Registers a periodic cron job in memory."""
        self._cron_jobs[name] = CronJobMetadata(
            name=name,
            schedule_spec=schedule_spec,
            callable_or_code=callable_or_code,
            description=description,
            is_custom=is_custom,
            status=status
        )
        logger.debug(f"Cron job '{name}' [{schedule_spec}] registered.")

    async def start_cron_job(self, name: str) -> bool:
        """Starts the ticker loop for a specific cron job."""
        job = self._cron_jobs.get(name)
        if not job:
            return False

        if job.task and not job.task.done():
            return True

        def parse_interval(spec_str: str) -> float:
            try:
                return float(spec_str)
            except ValueError:
                import re
                m = re.search(r'(\d+)', spec_str)
                return float(m.group(1)) if m else 60.0

        async def _cron_wrapper():
            job.status = "active"
            interval = parse_interval(job.schedule_spec)
            while True:
                try:
                    await asyncio.sleep(interval)
                    job.last_run = int(time.time())
                    if self.db:
                        await self.db.update_cron_last_run(job.name, job.last_run)

                    if callable(job.callable_or_code):
                        if inspect.iscoroutinefunction(job.callable_or_code):
                            await job.callable_or_code()
                        else:
                            res = job.callable_or_code()
                            if inspect.isawaitable(res):
                                await res
                    elif isinstance(job.callable_or_code, str):
                        compiled_func = compile_custom_tool(job.name, job.callable_or_code)
                        if inspect.iscoroutinefunction(compiled_func):
                            await compiled_func()
                        else:
                            res = compiled_func()
                            if inspect.isawaitable(res):
                                await res
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error executing cron job '{name}': {str(e)}")

            job.status = "stopped"

        job.task = asyncio.create_task(_cron_wrapper())
        job.status = "active"
        return True

    async def stop_cron_job(self, name: str) -> bool:
        """Stops a cron job ticker task."""
        job = self._cron_jobs.get(name)
        if not job:
            return False

        if job.task and not job.task.done():
            job.task.cancel()
            try:
                await job.task
            except asyncio.CancelledError:
                pass

        job.status = "stopped"
        return True

    # --- DB SYNC & LISTING ---
    async def sync_with_db(self):
        """Syncs custom services and cron jobs from SQLite database at startup."""
        if not self.db:
            return

        logger.info("Synchronizing custom services and cron jobs from database...")
        try:
            db_services = await self.db.get_all_custom_services()
            for s_data in db_services:
                name = s_data["name"]
                code = s_data["code"]
                desc = s_data.get("description", "")
                status = s_data.get("status", "stopped")
                
                self.register_service(name, code, description=desc, is_custom=True, status=status)
                if status == "running":
                    await self.start_service(name)

            db_cron = await self.db.get_all_custom_cron_jobs()
            for c_data in db_cron:
                name = c_data["name"]
                spec = c_data["schedule_spec"]
                code = c_data["code"]
                desc = c_data.get("description", "")
                status = c_data.get("status", "active")
                
                self.register_cron_job(name, spec, code, description=desc, is_custom=True, status=status)
                if status == "active":
                    await self.start_cron_job(name)

            logger.info("Service and Cron DB synchronization complete.")
        except Exception as e:
            logger.error(f"Error syncing services/cron from DB: {str(e)}")

    def list_services(self) -> List[Dict[str, Any]]:
        """Returns a list of all registered services and statuses."""
        res = []
        for s in self._services.values():
            res.append({
                "name": s.name,
                "description": s.description,
                "is_custom": s.is_custom,
                "status": s.status
            })
        return res

    def list_cron_jobs(self) -> List[Dict[str, Any]]:
        """Returns a list of all registered cron jobs and statuses."""
        res = []
        for c in self._cron_jobs.values():
            res.append({
                "name": c.name,
                "schedule_spec": c.schedule_spec,
                "description": c.description,
                "is_custom": c.is_custom,
                "status": c.status,
                "last_run": c.last_run
            })
        return res


# Global singleton instance
service_manager = ServiceManager()
