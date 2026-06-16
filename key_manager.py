# key_manager.py
import logging
import json
import time
import httpx
from google import genai
from config import GEMINI_KEYS, POLLINATIONS_KEYS, GEMINI_MODELS, INPUT_TOKEN_LIMIT, OUTPUT_LENGTH, KEY_INFO_TIMEOUT, GEMINI_FREE_RECOVERY_TIME, GEMINI_PRO_RECOVERY_TIME, POLLINATIONS_KEY_RECOVERY_TIME
logger = logging.getLogger("KeyManager")


class GeminiKeyManager:
    def __init__(self, db_manager=None):
        self.keys = GEMINI_KEYS
        self.models = GEMINI_MODELS
        self.current_key_index = 0
        self.current_model_index = 0
        self.db = db_manager
        
        self.input_token_limit = INPUT_TOKEN_LIMIT
        self.output_token_limit = OUTPUT_LENGTH
        self._client = None

    async def load_saved_index(self):
        """Asynchronously loads rotation indexes from the DB and initializes quota states."""
        if self.db:
            try:
                # Primary import of Gemini keys from .env to the database
                for key in self.keys:
                    meta = await self.db.get_key_meta(key)
                    if not meta:
                        await self.db.save_key_meta(key, "gemini", models_json=json.dumps(self.models))

                saved_key = await self.db.get_memory("gemini_working_key_index")
                if saved_key is not None and self.keys:
                    self.current_key_index = int(saved_key) % len(self.keys)
                
                saved_model = await self.db.get_memory("gemini_working_model_index")
                if saved_model is not None and self.models:
                    self.current_model_index = int(saved_model) % len(self.models)
            except Exception as e:
                logger.error(f"Error loading Gemini indexes from DB: {str(e)}")
        await self._init_client()

    async def _init_client(self):
        # Auto-detect cooldown depending on the model class (Flash/Pro)
        current_model = self.get_model().lower()
        is_pro = any(x in current_model for x in ["pro", "thinking", "ultra", "experimental"])
        recovery_time = GEMINI_PRO_RECOVERY_TIME if is_pro else GEMINI_FREE_RECOVERY_TIME

        now = int(time.time())
        active_key = None
        
        # Looking for the first working active key taking into account the dynamic cooldown
        for i in range(len(self.keys)):
            idx = (self.current_key_index + i) % len(self.keys)
            key = self.keys[idx]
            meta = await self.db.get_key_meta(key) if self.db else None
            
            if meta and meta.get("status") == "exhausted":
                ex_at = meta.get("exhausted_at") or 0
                if (now - ex_at) >= recovery_time:
                    logger.info(f"Gemini key limits {key[:10]}... for model '{current_model}' updated by time ({recovery_time // 3600}h). Resetting status to active.")
                    await self.db.save_key_meta(key, "gemini", status="active", exhausted_at=None)
                    active_key = key
                    self.current_key_index = idx
                    break
            else:
                active_key = key
                self.current_key_index = idx
                break
                
        if not active_key:
            logger.warning("All Gemini keys are marked as exhausted. Fallback to the first key.")
            active_key = self.keys[0]
            self.current_key_index = 0

        self._client = genai.Client(api_key=active_key)
        if self.db:
            await self.db.save_key_meta(active_key, "gemini", last_used_at=now)

        current_model_str = self.get_model()
        try:
            model_info = self._client.models.get(model=current_model_str)
            if INPUT_TOKEN_LIMIT is None:
                self.input_token_limit = getattr(model_info, 'input_token_limit', 1000000)
            if OUTPUT_LENGTH is None:
                self.output_token_limit = getattr(model_info, 'output_token_limit', 8192)
            
            if self.db:
                raw_info = {"input_limit": self.input_token_limit, "output_limit": self.output_token_limit}
                await self.db.save_key_meta(active_key, "gemini", raw_info_json=json.dumps(raw_info))
        except Exception as e:
            logger.warning(f"Failed to get metadata for model '{current_model_str}': {str(e)}")
            if self.input_token_limit is None:
                self.input_token_limit = 1000000
            if self.output_token_limit is None:
                self.output_token_limit = 8192

    def get_client(self):
        return self._client

    def get_model(self) -> str:
        if not self.models:
            return "gemini-3.1-flash-lite"
        return self.models[self.current_model_index]

    async def mark_key_exhausted(self):
        """Marks the current key as exhausted in the DB."""
        if self.db:
            active_key = self.keys[self.current_key_index]
            await self.db.save_key_meta(active_key, "gemini", status="exhausted", exhausted_at=int(time.time()))

    async def rotate_async(self):
        """First rotates the model, and after a full cycle switches the key."""
        await self.mark_key_exhausted() # Mark the current key as exhausted before rotation
        
        if len(self.models) > 1:
            self.current_model_index = (self.current_model_index + 1) % len(self.models)
            if self.current_model_index == 0 and len(self.keys) > 1:
                self.current_key_index = (self.current_key_index + 1) % len(self.keys)
        else:
            if len(self.keys) > 1:
                self.current_key_index = (self.current_key_index + 1) % len(self.keys)

        if self.db:
            try:
                await self.db.set_memory("gemini_working_key_index", str(self.current_key_index))
                await self.db.set_memory("gemini_working_model_index", str(self.current_model_index))
            except Exception as e:
                logger.error(f"Error saving Gemini rotation indexes: {str(e)}")
                
        await self._init_client()
        return self._client


class PollinationsKeyManager:
    def __init__(self, db_manager=None):
        self.keys = POLLINATIONS_KEYS
        self.current_index = 0
        self.db = db_manager

    async def load_saved_index(self):
        """Imports Pollinations keys into the DB, asynchronously requesting owners via API."""
        if self.db:
            try:
                for key in self.keys:
                    meta = await self.db.get_key_meta(key)
                    if not meta:
                        # Request key information via the official endpoint
                        owner, key_type, raw_info = await self.fetch_key_info_from_api(key)
                        await self.db.save_key_meta(
                            key_value=key,
                            provider="pollinations",
                            key_type=key_type,
                            owner=owner,
                            raw_info_json=json.dumps(raw_info)
                        )

                saved = await self.db.get_memory("pollinations_working_key_index")
                if saved is not None and self.keys:
                    self.current_index = int(saved) % len(self.keys)
            except Exception as e:
                logger.error(f"Error initializing Pollinations keys: {str(e)}")

    async def fetch_key_info_from_api(self, key: str) -> tuple:
        """Makes a GET request to the Pollinations API to validate and retrieve the owner/limits."""
        url = "https://gen.pollinations.ai/account/key"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=KEY_INFO_TIMEOUT) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # Parse owner (owner/user), key type and remaining limit
                    owner = data.get("owner") or data.get("user_id") or f"user_{key[:10]}"
                    key_type = data.get("type") or ("secret" if key.startswith("sk_") else "publishable")
                    return owner, key_type, data
        except Exception as e:
            logger.debug(f"Failed to get Pollinations key metadata via API: {str(e)}")
        
        # Fallback based on prefixes
        fallback_owner = f"user_{key[:10]}"
        fallback_type = "secret" if key.startswith("sk_") else "publishable"
        return fallback_owner, fallback_type, {}

    def get_key(self) -> str:
        if not self.keys:
            return ""
        return self.keys[self.current_index]

    async def get_active_key(self) -> str:
        """Returns the first working active key, resetting limits on schedule."""
        if not self.keys:
            return ""
            
        now = int(time.time())
        for i in range(len(self.keys)):
            idx = (self.current_index + i) % len(self.keys)
            key = self.keys[idx]
            meta = await self.db.get_key_meta(key) if self.db else None
            
            if meta and meta.get("status") == "exhausted":
                ex_at = meta.get("exhausted_at") or 0
                if (now - ex_at) >= POLLINATIONS_KEY_RECOVERY_TIME:
                    logger.info(f"Pollinations key limits {key[:10]}... updated. Resetting to active.")
                    # Reset status for all keys of the same owner
                    owner = meta.get("owner")
                    if owner:
                        await self.db.update_keys_status_by_owner(owner, "active", None)
                    else:
                        await self.db.save_key_meta(key, "pollinations", status="active", exhausted_at=None)
                    self.current_index = idx
                    return key
            else:
                self.current_index = idx
                return key
                
        return self.keys[self.current_index]

    async def mark_current_key_exhausted(self):
        """Batch marks all keys of the current owner as exhausted in the DB."""
        if not self.db:
            return
        key = self.get_key()
        meta = await self.db.get_key_meta(key)
        now = int(time.time())
        if meta:
            owner = meta.get("owner")
            if owner:
                logger.warning(f"Batch blocking of all keys of owner '{owner}' due to exhaustion of limits.")
                await self.db.update_keys_status_by_owner(owner, "exhausted", now)
            else:
                await self.db.save_key_meta(key, "pollinations", status="exhausted", exhausted_at=now)

    async def rotate_key_async(self) -> str:
        await self.mark_current_key_exhausted() # Block the current owner's pool
        
        if not self.keys or len(self.keys) <= 1:
            return self.get_key()

        self.current_index = (self.current_index + 1) % len(self.keys)
        
        if self.db:
            try:
                await self.db.set_memory("pollinations_working_key_index", str(self.current_index))
            except Exception as e:
                logger.error(f"Error saving Pollinations index: {str(e)}")
                
        return await self.get_active_key()
