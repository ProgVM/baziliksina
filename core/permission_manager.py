# core/permission_manager.py
import logging
import json
from typing import Optional, Union, Dict, Any, List
import config
from utils import matches_filter

logger = logging.getLogger("PermissionManager")

class RankLevel:
    ROOT_ADMIN = 100
    ADMIN = 80
    PRIORITY = 50
    USER = 10
    BLOCKED = 0

class PermissionManager:
    """
    A unified permission and rank management engine that evaluates user hierarchy,
    immutable root admins, dynamic database ranks, and the granular AI CRUD+INVOKE matrix.
    """
    def __init__(self, db_manager=None):
        self.db = db_manager

    def set_db_manager(self, db_manager):
        """Binds the active SQLite database manager instance."""
        self.db = db_manager

    async def get_user_rank_info(self, user_id: Union[int, str], username: Optional[str] = None) -> Dict[str, Any]:
        """
        Resolves the exact rank level and explicit permissions list for a user.
        Checks immutable config ADMINS first, then DB user_ranks, then blacklists/whitelists.
        """
        u_id = int(user_id) if str(user_id).isdigit() else user_id
        u_id_str = str(u_id)
        u_name = f"@{username.lstrip('@').lower()}" if username else ""

        # 1. Check Creator / Config ADMINS (Immutable Base Ranks)
        admins_cfg = getattr(config, "ADMINS", {})
        if isinstance(admins_cfg, dict):
            # Search by integer or string ID
            if u_id in admins_cfg or u_id_str in admins_cfg:
                cfg_data = admins_cfg.get(u_id) or admins_cfg.get(u_id_str)
                if isinstance(cfg_data, dict):
                    return {
                        "rank": int(cfg_data.get("rank", RankLevel.ROOT_ADMIN)),
                        "permissions": cfg_data.get("permissions", ["all"]),
                        "source": "config_admins"
                    }
                elif isinstance(cfg_data, (int, float)):
                    return {"rank": int(cfg_data), "permissions": ["all"], "source": "config_admins"}

            # Search by @username
            if u_name:
                for key, data in admins_cfg.items():
                    if str(key).lower() == u_name or (isinstance(data, dict) and data.get("username", "").lower() == u_name):
                        rank_val = data.get("rank", RankLevel.ROOT_ADMIN) if isinstance(data, dict) else int(data)
                        perms = data.get("permissions", ["all"]) if isinstance(data, dict) else ["all"]
                        return {"rank": int(rank_val), "permissions": perms, "source": "config_admins"}

        # 2. Check Blacklists (Instant Block)
        user_blacklist = getattr(config, "USER_CACHE_BLACKLIST", [])
        if u_id in user_blacklist or u_id_str in user_blacklist or (u_name and u_name in user_blacklist):
            return {"rank": RankLevel.BLOCKED, "permissions": [], "source": "blacklist"}

        # 3. Check SQLite DB Ranks (`user_ranks` table)
        if self.db:
            try:
                db_rank_data = await self.db.get_user_rank(u_id_str)
                if not db_rank_data and u_name:
                    db_rank_data = await self.db.get_user_rank(u_name)

                if db_rank_data:
                    return {
                        "rank": int(db_rank_data.get("rank", RankLevel.USER)),
                        "permissions": db_rank_data.get("permissions", []),
                        "source": "database"
                    }
            except Exception as e:
                logger.error(f"Error reading user rank from DB: {str(e)}")

        # 4. Check Whitelists / Priority
        user_whitelist = getattr(config, "USER_CACHE_WHITELIST", [])
        if user_whitelist and (u_id in user_whitelist or u_id_str in user_whitelist or u_name in user_whitelist):
            return {"rank": RankLevel.PRIORITY, "permissions": [], "source": "whitelist"}

        # 5. Default User Rank
        return {"rank": RankLevel.USER, "permissions": [], "source": "default"}

    async def get_user_rank(self, user_id: Union[int, str], username: Optional[str] = None) -> int:
        """Returns the numerical rank integer for a user."""
        info = await self.get_user_rank_info(user_id, username)
        return info["rank"]

    async def has_permission(
        self, 
        user_id: Union[int, str], 
        required_rank: int = RankLevel.ADMIN, 
        required_perm: Optional[str] = None, 
        username: Optional[str] = None
    ) -> bool:
        """
        Checks if a user satisfies either the required numerical rank OR has explicit permission string.
        """
        info = await self.get_user_rank_info(user_id, username)
        user_rank = info["rank"]
        user_perms = info["permissions"]

        if user_rank == RankLevel.BLOCKED:
            return False

        if "all" in user_perms:
            return True

        if required_perm and required_perm in user_perms:
            return True

        return user_rank >= required_rank

    async def can_promote_or_demote(
        self, 
        actor_id: Union[int, str], 
        target_id: Union[int, str], 
        new_rank: int, 
        actor_username: Optional[str] = None, 
        target_username: Optional[str] = None
    ) -> tuple[bool, str]:
        """
        Validates if actor_id has sufficient rank to alter target_id's rank.
        Protects immutable config admins from demotion below their base rank.
        """
        actor_info = await self.get_user_rank_info(actor_id, actor_username)
        target_info = await self.get_user_rank_info(target_id, target_username)

        actor_rank = actor_info["rank"]
        target_rank = target_info["rank"]

        if actor_rank < RankLevel.ADMIN:
            return False, "Permission denied: Actor rank is below ADMIN level."

        if actor_rank <= target_rank and actor_rank < RankLevel.ROOT_ADMIN:
            return False, f"Permission denied: You cannot alter rank of user with equal/higher rank ({target_rank})."

        if target_info["source"] == "config_admins":
            cfg_base_rank = target_info["rank"]
            if new_rank < cfg_base_rank:
                return False, f"Permission denied: Target user is a root config admin with immutable base rank {cfg_base_rank}."

        return True, "Authorized"

    # --- AI GRANULAR CRUD + INVOKE MATRIX ---
    def can_ai_perform(self, element: str, action: str) -> bool:
        """
        Checks if AI has permission to perform an action (CREATE, EDIT, DELETE, VIEW_INFO, VIEW_CONTENT, LIST, INVOKE)
        on a specific element category (COMMANDS, TOOLS, TAGS, SERVICES, CRON, SITES).
        """
        elem = str(element).upper().strip()
        act = str(action).upper().strip()

        param_name = f"AI_PERM_{elem}_{act}"
        if hasattr(config, param_name):
            return bool(getattr(config, param_name))

        logger.warning(f"Unknown AI permission check requested: {param_name}. Defaulting to False.")
        return False

    def can_ai_use_pipeline_operator(self, operator_str: str) -> bool:
        """
        Checks if AI is allowed to use a specific pipeline chaining operator (;, &&, ||, |).
        """
        if not getattr(config, "AI_ALLOW_PIPELINES", True):
            return False

        op = str(operator_str).strip()
        allowed_ops = getattr(config, "AI_ALLOWED_PIPELINE_OPERATORS", [])
        blocked_ops = getattr(config, "AI_BLOCKED_PIPELINE_OPERATORS", [])

        return matches_filter(op, allowed_ops, blocked_ops, default_allow=True)


# Global singleton instance
permission_manager = PermissionManager()
