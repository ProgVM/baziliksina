# core/key_manager.py
"""
===============================================================================
ULTRA-FAST ASYNC DYNAMIC POOL MANAGER & MULTI-MODEL CASCADING ENGINE
Features:
- 36 Independent Project Quota Scopes (GEMINI_A{1..6}_P{1..6}, GEMINI_API_KEYS, etc.)
- Exact Google API RPC Error & Quota Parser (Protobuf JSON / RetryInfo)
- Dynamic O(1) Lock-free Client Acquisition with Zero Unnecessary Sleep Delays
- Sliding Window TPM / RPM Tracking with Automatic 250k TPM Load Balancing
- Immediate Model Cascade on 503 Capacity Outages (15-60s quarantine penalty)
- Non-Blocking 429 Key Switching
- Full SQLite DB Persistence for Key Metadata and Working Indexes
- Native PollinationsKeyManager integration
===============================================================================
"""

import os
import time
import json
import ast
import re
import asyncio
import datetime
import logging
from collections import deque
from typing import Optional, Dict, Any, List, Tuple, Set

import httpx
from google import genai
from google.genai import types

import config
from proxy_manager import proxy_rotator

logger = logging.getLogger("KeyManager")


def get_seconds_until_pacific_midnight() -> int:
    """Calculates the exact number of seconds remaining until midnight Pacific Time (US/Pacific)."""
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        is_pdt = False
        if 3 < now_utc.month < 11:
            is_pdt = True
        elif now_utc.month == 3 and now_utc.day >= 14:
            is_pdt = True
        elif now_utc.month == 11 and now_utc.day < 7:
            is_pdt = True
        offset_hours = getattr(config, "PACIFIC_DAYLIGHT_TIME_OFFSET", -7) if is_pdt else getattr(config, "PACIFIC_STANDARD_TIME_OFFSET", -8)
        pacific_tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
        pacific_now = now_utc.astimezone(pacific_tz)
        tomorrow_pacific = datetime.datetime(
            year=pacific_now.year,
            month=pacific_now.month,
            day=pacific_now.day,
            hour=0,
            minute=0,
            second=0,
            tzinfo=pacific_tz
        ) + datetime.timedelta(days=1)
        min_sec = int(getattr(config, "GEMINI_MIN_COOLDOWN_SECONDS", 60))
        return max(min_sec, int((tomorrow_pacific - pacific_now).total_seconds()))
    except Exception as e:
        logger.warning(f"Error calculating Pacific Midnight: {e}")
        return getattr(config, "GEMINI_DAILY_LIMIT_COOLDOWN", 86400)


def parse_gemini_error_cooldown(ex_or_str: Any) -> Tuple[float, bool, Dict[str, Any]]:
    """
    Parses Google API RPC Error payload / JSON string to extract exact cooldowns,
    differentiating between daily quotas (RPD) and minute rate limits (RPM / TPM).
    Returns (cooldown_seconds, is_daily, quota_meta).
    """
    ex_str = str(ex_or_str)
    err_lower = ex_str.lower()
    is_daily = False
    retry_delay_val: Optional[float] = None
    quota_metric: Optional[str] = None
    quota_value: Optional[int] = None
    quota_id: Optional[str] = None
    target_model: Optional[str] = None

    def _inspect_node(node):
        nonlocal is_daily, retry_delay_val, quota_metric, quota_value, quota_id, target_model
        if isinstance(node, dict):
            t_url = str(node.get("@type", "") or node.get("typeUrl", "")).lower()
            if any(w in t_url for w in ["quota", "errorinfo", "details", "quotafailure", "retryinfo"]):
                violations = node.get("violations", [])
                if isinstance(violations, list):
                    for v in violations:
                        if isinstance(v, dict):
                            desc = str(v.get("description", "")).lower()
                            q_m = v.get("quotaMetric", "") or v.get("metric", "")
                            q_i = v.get("quotaId", "") or v.get("id", "")
                            q_v = v.get("quotaValue", "") or v.get("limit", "")
                            q_dims = v.get("quotaDimensions", {}) or v.get("dimensions", {})

                            if any(w in desc for w in ["perday", "daily", "per_day", "day-freetier", "day_free_tier"]):
                                is_daily = True
                            if q_m:
                                quota_metric = str(q_m)
                            if q_i:
                                quota_id = str(q_i)
                                if any(w in q_i.lower() for w in ["perday", "day", "daily"]):
                                    is_daily = True
                            if q_v is not None:
                                try:
                                    quota_value = int(q_v)
                                except (ValueError, TypeError):
                                    pass
                            if isinstance(q_dims, dict) and "model" in q_dims:
                                target_model = str(q_dims["model"])

            q_id = str(node.get("quotaId", "")).lower()
            q_metric = str(node.get("quotaMetric", "")).lower()
            q_val = str(node.get("quotaValue", ""))

            if any(w in q_id for w in ["perday", "per_day", "day-freetier", "generaterequestsperday"]):
                is_daily = True
            if any(w in q_metric for w in ["perday", "per_day", "day-freetier", "requestsperday"]):
                is_daily = True
            if q_val in ["20", "50"] and ("perday" in q_id or "day" in q_id or "requestsperday" in q_metric):
                is_daily = True

            rd = node.get("retryDelay") or node.get("retry_delay")
            if rd is not None:
                try:
                    if isinstance(rd, str) and rd.endswith("s"):
                        retry_delay_val = float(rd[:-1])
                    elif isinstance(rd, (int, float)):
                        retry_delay_val = float(rd)
                except Exception:
                    pass

            for v in node.values():
                if isinstance(v, (dict, list)):
                    _inspect_node(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    _inspect_node(item)

    dict_match = re.search(r"(\{[\s\S]*\})", ex_str)
    if dict_match:
        payload_str = dict_match.group(1)
        parsed_obj = None
        try:
            parsed_obj = json.loads(payload_str)
        except Exception:
            try:
                parsed_obj = ast.literal_eval(payload_str)
            except Exception:
                pass

        if isinstance(parsed_obj, dict):
            _inspect_node(parsed_obj)

    if retry_delay_val is None:
        delay_patterns = [
            r'["\']?retryDelay["\']?\s*[:=]\s*["\']?([0-9]+(?:\.[0-9]+)?)\s*s?["\']?',
            r'retry\s*(?:after|in)\s*([0-9]+(?:\.[0-9]+)?)\s*s(?:econds?)?',
            r'wait\s*([0-9]+(?:\.[0-9]+)?)\s*s(?:econds?)?',
            r'try\s*again\s*in\s*([0-9]+(?:\.[0-9]+)?)\s*s(?:econds?)?',
            r'quota\s*will\s*reset\s*in\s*([0-9]+(?:\.[0-9]+)?)\s*s(?:econds?)?'
        ]
        for pat in delay_patterns:
            m = re.search(pat, ex_str, re.IGNORECASE)
            if m:
                try:
                    retry_delay_val = float(m.group(1))
                    break
                except Exception:
                    pass

    if not is_daily:
        daily_indicators = [
            "generaterequestsperday",
            "requestsperday",
            "requests per day",
            "daily quota",
            "perday",
            "per day",
            "day-freetier",
            "day_free_tier"
        ]
        if any(ind in err_lower for ind in daily_indicators):
            is_daily = True

    if quota_value is None:
        m_val = re.search(r'["\']?quotaValue["\']?\s*[:=]\s*["\']?(\d+)["\']?', ex_str)
        if m_val:
            try:
                quota_value = int(m_val.group(1))
            except Exception:
                pass

    if not target_model:
        m_model = re.search(r'["\']?model["\']?\s*[:=]\s*["\']?([a-zA-Z0-9\.\-_]+)["\']?', ex_str)
        if m_model:
            target_model = m_model.group(1)

    quota_meta = {
        "quota_metric": quota_metric,
        "quota_value": quota_value,
        "quota_id": quota_id,
        "target_model": target_model,
        "retry_delay_sec": retry_delay_val
    }

    if is_daily:
        cooldown = float(get_seconds_until_pacific_midnight())
        logger.warning(f"Daily quota reached ({quota_id or 'RPD'}). Key cooldown set to midnight reset ({cooldown:.0f}s).")
        return cooldown, True, quota_meta

    if retry_delay_val is not None and retry_delay_val > 0:
        cooldown = float(retry_delay_val) + 0.5
        return cooldown, False, quota_meta

    cooldown = 15.0 if ("limit" in err_lower or "exhausted" in err_lower) else 5.0
    return cooldown, False, quota_meta


class GeminiKeyManager:
    """
    Ultra-Fast Non-Blocking Dynamic Key Pool Coordinator and Multi-Tier Model Cascading Engine.
    Features:
    - 36 Independent Project Quota Scopes (GEMINI_A{1..6}_P{1..6}, GEMINI_API_KEYS, etc.)
    - Exact Google API RPC Error & Quota Parser (Protobuf JSON / RetryInfo)
    - Dynamic O(1) Lock-free Client Acquisition with Zero Unnecessary Sleep Delays
    - Sliding Window TPM / RPM Tracking with Automatic 250k TPM Load Balancing
    - Immediate Model Cascade on 503 Capacity Outages (15-60s quarantine penalty)
    - Non-Blocking 429 Key Switching
    - Full SQLite DB Persistence for Key Metadata and Working Indexes
    """
    def __init__(self, db_manager=None):
        self.db = db_manager
        self.keys: List[str] = []
        self._load_keys()

        raw_models = getattr(config, "GEMINI_MODELS", [])
        if isinstance(raw_models, (list, tuple)):
            self.models = [str(m).strip() for m in raw_models if str(m).strip()]
        elif isinstance(raw_models, str):
            self.models = [m.strip() for m in raw_models.split(",") if m.strip()]
        else:
            self.models = []

        if not self.models:
            self.models = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-flash-lite"]

        self.invalid_models: Set[str] = set()
        self.model_daily_exhaustion: Dict[str, float] = {}
        self.model_congestion: Dict[str, float] = {}

        self.current_key_index = 0
        self.current_model_index = 0

        self.input_token_limit = getattr(config, "INPUT_TOKEN_LIMIT", 1000000)
        self.output_token_limit = getattr(config, "OUTPUT_LENGTH", 8192)

        # High-performance key & model state tracking
        self.exhausted_keys: Dict[Tuple[int, str], float] = {}
        self.request_history: Dict[int, deque] = {i: deque() for i in range(len(self.keys))}
        self.token_history: Dict[int, deque] = {i: deque() for i in range(len(self.keys))}
        self.daily_request_counts: Dict[Tuple[int, str], int] = {}
        self.daily_request_day: int = datetime.datetime.now(datetime.timezone.utc).day

        self.consecutive_429_count = 0
        self.consecutive_503_count = 0
        self.last_429_timestamp = 0.0
        self.consecutive_geo_blocked_count = 0
        self.discovered_model_limits: Dict[str, Dict[str, Any]] = {}

        self._clients_cache: Dict[str, genai.Client] = {}
        self._client: Optional[genai.Client] = None
        self._init_client()

    def _load_keys(self):
        """Loads API keys from config, environment variables, 36 project scopes, and cloud runtimes."""
        # 1. Config lists
        for attr_name in ["GEMINI_KEYS", "GEMINI_API_KEYS"]:
            raw_cfg = getattr(config, attr_name, None)
            if raw_cfg:
                if isinstance(raw_cfg, (list, tuple, set)):
                    for k in raw_cfg:
                        k_clean = str(k).strip()
                        if k_clean and len(k_clean) >= 20 and k_clean not in self.keys:
                            self.keys.append(k_clean)
                elif isinstance(raw_cfg, str):
                    for k in raw_cfg.split(","):
                        k_clean = k.strip()
                        if k_clean and len(k_clean) >= 20 and k_clean not in self.keys:
                            self.keys.append(k_clean)

        # 2. Environment variables: GEMINI_KEYS, GEMINI_API_KEYS, GEMINI_API_KEY
        for env_var in ["GEMINI_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY"]:
            raw_env = os.getenv(env_var, "")
            if raw_env:
                for k in raw_env.split(","):
                    k_clean = k.strip()
                    if k_clean and len(k_clean) >= 20 and k_clean not in self.keys:
                        self.keys.append(k_clean)

        # 3. 36 project scopes: GEMINI_A{1..6}_P{1..6}
        for acc in range(1, 7):
            for proj in range(1, 7):
                env_name = f"GEMINI_A{acc}_P{proj}"
                val = os.getenv(env_name)
                if val and val.strip():
                    val_clean = val.strip()
                    if len(val_clean) >= 20 and val_clean not in self.keys:
                        self.keys.append(val_clean)

        # 4. Kaggle secrets / Colab userdata (if available)
        try:
            from kaggle_secrets import UserSecretsClient
            secrets = UserSecretsClient()
            k_sec = secrets.get_secret("GEMINI_API_KEYS") or secrets.get_secret("GEMINI_KEYS") or secrets.get_secret("GEMINI_API_KEY")
            if k_sec:
                for k in str(k_sec).split(","):
                    k_clean = k.strip()
                    if k_clean and len(k_clean) >= 20 and k_clean not in self.keys:
                        self.keys.append(k_clean)
        except Exception:
            pass

        try:
            from google.colab import userdata
            k_colab = userdata.get("GEMINI_API_KEYS") or userdata.get("GEMINI_KEYS") or userdata.get("GEMINI_API_KEY")
            if k_colab:
                for k in str(k_colab).split(","):
                    k_clean = k.strip()
                    if k_clean and len(k_clean) >= 20 and k_clean not in self.keys:
                        self.keys.append(k_clean)
        except Exception:
            pass

        if not self.keys:
            logger.error("CRITICAL: Zero valid Gemini API keys found in configuration or environment!")
            self.keys.append("AIzaSyDummyKey_PleaseConfigureYourKeysInEnvironment_")
        else:
            logger.info(f"Loaded {len(self.keys)} unique Gemini API keys into resilient pool.")

    def _get_client_for_key(self, key_idx: int) -> genai.Client:
        key_idx = key_idx % len(self.keys)
        active_key = self.keys[key_idx]

        proxy_url = None
        try:
            if hasattr(proxy_rotator, "get_proxy"):
                proxy_url = proxy_rotator.get_proxy("gemini") or proxy_rotator.get_proxy()
            elif hasattr(proxy_rotator, "get_healthy_proxy"):
                proxy_url = proxy_rotator.get_healthy_proxy()
        except Exception:
            pass

        cache_key = f"{key_idx}:{proxy_url or 'direct'}"
        if cache_key in self._clients_cache:
            return self._clients_cache[cache_key]

        if proxy_url:
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["ALL_PROXY"] = proxy_url
            os.environ["all_proxy"] = proxy_url

        http_options = None
        if proxy_url:
            try:
                http_options = types.HttpOptions(
                    client_args={"proxy": proxy_url},
                    async_client_args={"proxy": proxy_url}
                )
            except Exception:
                http_options = {"proxy": proxy_url}

        client = genai.Client(api_key=active_key, http_options=http_options)
        self._clients_cache[cache_key] = client
        return client

    def _init_client(self):
        if not self.keys:
            return
        self._client = self._get_client_for_key(self.current_key_index)

    async def load_saved_index(self):
        """Asynchronously loads rotation indexes and quota states from the DB."""
        now = time.time()
        if self.db:
            try:
                for key in self.keys:
                    meta = await self.db.get_key_meta(key)
                    if not meta:
                        await self.db.save_key_meta(key, "gemini", models_json=json.dumps(self.models))
                    elif meta.get("status") == "exhausted":
                        ex_at = meta.get("exhausted_at") or 0
                        cooldown = 18000
                        raw_info_raw = meta.get("raw_info_json")
                        if raw_info_raw:
                            try:
                                raw_info = json.loads(raw_info_raw)
                                if raw_info.get("cooldown_duration"):
                                    cooldown = float(raw_info["cooldown_duration"])
                            except Exception:
                                pass
                        if (now - ex_at) < cooldown:
                            key_idx = self.keys.index(key) if key in self.keys else -1
                            if key_idx != -1:
                                for m in self.models:
                                    self.exhausted_keys[(key_idx, m)] = ex_at + cooldown
                        else:
                            await self.db.save_key_meta(key, "gemini", status="active", exhausted_at=None)

                saved_key = await self.db.get_memory("gemini_working_key_index")
                if saved_key is not None and self.keys:
                    self.current_key_index = int(saved_key) % len(self.keys)

                saved_model = await self.db.get_memory("gemini_working_model_index")
                if saved_model is not None and self.models:
                    self.current_model_index = int(saved_model) % len(self.models)
            except Exception as e:
                logger.error(f"Error loading Gemini indexes from DB: {str(e)}")

        self._init_client()

    async def rotate_proxy_async(self) -> bool:
        """Rotates the active Gemini proxy to the next available one and re-initializes client."""
        next_proxy = None
        try:
            if hasattr(proxy_rotator, "rotate_proxy"):
                next_proxy = proxy_rotator.rotate_proxy("gemini")
            elif hasattr(proxy_rotator, "get_healthy_proxy"):
                next_proxy = proxy_rotator.get_healthy_proxy()
        except Exception as e:
            logger.warning(f"Error rotating proxy: {e}")

        if next_proxy:
            logger.info(f"Gemini proxy rotated to: {next_proxy}. Re-initializing client...")
            self._clients_cache.clear()
            self._init_client()
            return True
        return False

    def get_client(self) -> genai.Client:
        """Returns active client, automatically switching away from any key currently on cooldown."""
        now = time.time()
        self._prune_sliding_windows(now)
        curr_m = self.get_model()
        if self._client is None or now < self.exhausted_keys.get((self.current_key_index, curr_m), 0.0):
            best_idx, best_model = self.select_best_key_and_model()
            self.current_key_index = best_idx
            if best_model in self.models:
                self.current_model_index = self.models.index(best_model)
            self._client = self._get_client_for_key(self.current_key_index)
        return self._client

    def get_model(self) -> str:
        """Returns the currently active model from cascade list, skipping invalid/congested ones."""
        now = time.time()
        self._prune_sliding_windows(now)
        valid_models = [
            m for m in self.models
            if m not in self.invalid_models
            and m not in self.model_daily_exhaustion
            and now >= self.model_congestion.get(m, 0.0)
        ]
        if not valid_models:
            valid_models = [m for m in self.models if m not in self.invalid_models and m not in self.model_daily_exhaustion]
            if not valid_models:
                self.model_daily_exhaustion.clear()
                valid_models = [m for m in self.models if m not in self.invalid_models]
                if not valid_models:
                    self.invalid_models.clear()
                    valid_models = self.models

        self.current_model_index = self.current_model_index % len(valid_models)
        return valid_models[self.current_model_index]

    def mark_model_invalid(self, model_name: str):
        """Marks a model as 404 (not found / deprecated) to skip it in cascade."""
        if model_name in self.models:
            self.invalid_models.add(model_name)
            logger.warning(f"Model '{model_name}' marked invalid (404). Active pool: {[m for m in self.models if m not in self.invalid_models]}")
            self.current_model_index = 0

    def rotate_to_next_model(self, reason: str = "Manual rotation") -> str:
        """Cascades to the next available model in priority list."""
        now = time.time()
        self._prune_sliding_windows(now)
        valid_models = [
            m for m in self.models
            if m not in self.invalid_models
            and m not in self.model_daily_exhaustion
            and now >= self.model_congestion.get(m, 0.0)
        ]
        if not valid_models:
            valid_models = [m for m in self.models if m not in self.invalid_models and m not in self.model_daily_exhaustion]
            if not valid_models:
                self.model_daily_exhaustion.clear()
                valid_models = [m for m in self.models if m not in self.invalid_models]

        if len(valid_models) <= 1:
            return self.get_model()

        old_model = self.get_model()
        curr_in_valid = valid_models.index(old_model) if old_model in valid_models else 0
        next_in_valid = (curr_in_valid + 1) % len(valid_models)
        new_model = valid_models[next_in_valid]
        self.current_model_index = self.models.index(new_model) if new_model in self.models else next_in_valid
        logger.warning(f"🔄 Model Cascade triggered: '{old_model}' -> '{new_model}'. Reason: {reason}.")
        self._client = self._get_client_for_key(self.current_key_index)
        return new_model

    def select_best_key_and_model(self, estimated_tokens: int = 35000) -> Tuple[int, str]:
        """
        Ultra-Fast Lock-Free Key & Model Selector.
        Iterates over models in cascade order, picking the first ready key with lowest rolling TPM load.
        """
        now = time.time()
        self._prune_sliding_windows(now)

        tpm_limit = getattr(config, "FREE_TIER_TPM_LIMIT", 250000)
        rpm_limit = getattr(config, "FREE_TIER_RPM_LIMIT", 15)

        candidate_models = [
            m for m in self.models
            if m not in self.invalid_models
            and m not in self.model_daily_exhaustion
            and now >= self.model_congestion.get(m, 0.0)
        ]
        if not candidate_models:
            candidate_models = [m for m in self.models if m not in self.invalid_models]
        if not candidate_models:
            candidate_models = list(self.models)

        for model in candidate_models:
            best_key = -1
            min_load = float("inf")

            for offset in range(len(self.keys)):
                idx = (self.current_key_index + offset) % len(self.keys)

                cooldown_until = self.exhausted_keys.get((idx, model), 0.0)
                if now < cooldown_until:
                    continue

                discovered = self.discovered_model_limits.get(model, {})
                if discovered.get("is_daily") and discovered.get("quota_value"):
                    daily_cap = discovered["quota_value"]
                    if self.daily_request_counts.get((idx, model), 0) >= daily_cap:
                        continue

                rolling_tokens = sum(tok for ts, tok in self.token_history.get(idx, ()) if now - ts < 60.0)
                rolling_requests = sum(1 for ts in self.request_history.get(idx, ()) if now - ts < 60.0)

                if rolling_tokens + estimated_tokens > tpm_limit:
                    continue

                base_rpm = rpm_limit
                if "3.8" in model or "ultra" in model:
                    base_rpm = min(5, rpm_limit)
                elif discovered.get("quota_value") and not discovered.get("is_daily") and discovered["quota_value"] <= 60:
                    base_rpm = min(discovered["quota_value"], rpm_limit)

                if rolling_requests >= base_rpm:
                    continue

                if rolling_tokens < min_load:
                    min_load = rolling_tokens
                    best_key = idx

            if best_key != -1:
                return best_key, model

        shortest_wait = float("inf")
        best_pair = (self.current_key_index, candidate_models[0] if candidate_models else self.models[0])

        for m in (candidate_models or self.models):
            m_wait = max(0.0, self.model_congestion.get(m, 0.0) - now)
            for k_i in range(len(self.keys)):
                k_wait = max(0.0, self.exhausted_keys.get((k_i, m), 0.0) - now)
                tot_wait = max(m_wait, k_wait)
                if tot_wait < shortest_wait:
                    shortest_wait = tot_wait
                    best_pair = (k_i, m)

        return best_pair

    def select_best_key(self, estimated_tokens: int = 35000, model: Optional[str] = None) -> int:
        best_idx, best_m = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
        return best_idx

    def record_request_tokens(self, key_idx: int, tokens: int):
        """Records token and request telemetry to accurately enforce sliding window TPM/RPM limits."""
        now = time.time()
        self.consecutive_429_count = 0
        self.consecutive_503_count = 0
        self.consecutive_geo_blocked_count = 0

        curr_day = datetime.datetime.now(datetime.timezone.utc).day
        if curr_day != self.daily_request_day:
            self.daily_request_counts.clear()
            self.daily_request_day = curr_day

        target_model = self.get_model()
        self.daily_request_counts[(key_idx, target_model)] = self.daily_request_counts.get((key_idx, target_model), 0) + 1

        if key_idx not in self.request_history:
            self.request_history[key_idx] = deque()
        if key_idx not in self.token_history:
            self.token_history[key_idx] = deque()
        self.request_history[key_idx].append(now)
        self.token_history[key_idx].append((now, tokens))

    def _prune_sliding_windows(self, now: float):
        """Prunes sliding window queues and expired cooldowns."""
        for idx in range(len(self.keys)):
            if idx in self.request_history:
                req_q = self.request_history[idx]
                while req_q and now - req_q[0] >= 60.0:
                    req_q.popleft()

            if idx in self.token_history:
                tok_q = self.token_history[idx]
                while tok_q and now - tok_q[0][0] >= 60.0:
                    tok_q.popleft()

        expired_models = [m for m, exp_ts in self.model_daily_exhaustion.items() if now >= exp_ts]
        for m in expired_models:
            del self.model_daily_exhaustion[m]

        expired_congestions = [m for m, exp_ts in self.model_congestion.items() if now >= exp_ts]
        for m in expired_congestions:
            del self.model_congestion[m]

        expired_keys = [k for k, exp_ts in self.exhausted_keys.items() if now >= exp_ts]
        for k in expired_keys:
            del self.exhausted_keys[k]

    def get_account_for_key(self, key_idx: int) -> int:
        return key_idx // 6

    def get_project_for_key(self, key_idx: int) -> int:
        return key_idx % 6

    def get_key_60s_tokens(self, key_idx: int, now: Optional[float] = None) -> int:
        if now is None:
            now = time.time()
        if key_idx in self.token_history:
            return sum(tok for ts, tok in self.token_history[key_idx] if now - ts < 60.0)
        return 0

    def get_pool_status(self) -> Dict[str, Any]:
        """Provides complete health and quota statistics of the key pool."""
        now = time.time()
        self._prune_sliding_windows(now)

        curr_m = self.get_model()
        active_ready = 0
        cooldown_count = 0

        for i in range(len(self.keys)):
            cooldown_until = self.exhausted_keys.get((i, curr_m), 0.0)
            if now >= cooldown_until:
                active_ready += 1
            else:
                cooldown_count += 1

        curr_idx = self.current_key_index
        key_rpm = len(self.request_history[curr_idx]) if curr_idx in self.request_history else 0
        key_tpm = sum(tok for ts, tok in self.token_history[curr_idx]) if curr_idx in self.token_history else 0

        return {
            "total_keys": len(self.keys),
            "active_ready_keys": active_ready,
            "cooldown_keys": cooldown_count,
            "current_key_index": curr_idx + 1,
            "current_key_idx": curr_idx,
            "current_account_index": self.get_account_for_key(curr_idx) + 1,
            "current_project_index": self.get_project_for_key(curr_idx) + 1,
            "current_key_60s_tokens": key_tpm,
            "current_key_60s_requests": key_rpm,
            "current_model": curr_m,
            "valid_models": [m for m in self.models if m not in self.invalid_models and m not in self.model_daily_exhaustion],
            "discovered_limits": self.discovered_model_limits,
            "consecutive_429": self.consecutive_429_count
        }

    def get_key_status_summary(self) -> Dict[str, Any]:
        return self.get_pool_status()

    def reset_all_cooldowns(self) -> int:
        return self.force_reset_cooldowns()

    def force_reset_cooldowns(self) -> int:
        """Forces immediate clearing of all cooldowns and resets counters."""
        count = len(self.exhausted_keys)
        self.exhausted_keys.clear()
        self.model_daily_exhaustion.clear()
        self.model_congestion.clear()
        self.invalid_models.clear()
        for i in range(len(self.keys)):
            if i in self.request_history:
                self.request_history[i].clear()
            if i in self.token_history:
                self.token_history[i].clear()
        self.consecutive_429_count = 0
        self.consecutive_503_count = 0
        self.consecutive_geo_blocked_count = 0
        logger.info("Resilient key pool cooldowns 100% reset and cleared.")
        return count

    async def get_ready_client_for_request(
        self,
        estimated_tokens: int = 35000,
        event_callback=None,
        agent=None
    ) -> genai.Client:
        """Resolves the best ready client and model for the upcoming request."""
        best_idx, best_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
        if best_model in self.models:
            self.current_model_index = self.models.index(best_model)
        self.current_key_index = best_idx
        self._client = self._get_client_for_key(self.current_key_index)
        return self._client

    async def handle_quota_exhausted(
        self,
        error_msg_or_ex: Any,
        event_callback=None,
        agent=None,
        estimated_tokens: int = 35000
    ) -> genai.Client:
        """
        Unified handler for 404, 401, 403, 500/502/503/504, and 429 quota errors.
        Parses exact RPC errors, penalties overloaded models, and selects next ready key instantly without sleep.
        """
        now = time.time()
        err_str = str(error_msg_or_ex)
        err_lower = err_str.lower()

        # 1. Model Not Found (404)
        if any(w in err_lower for w in ["404", "not found", "unsupported model"]):
            bad_model = self.get_model()
            self.mark_model_invalid(bad_model)
            best_idx, new_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
            self.current_key_index = best_idx
            if new_model in self.models:
                self.current_model_index = self.models.index(new_model)
            logger.info(f"Model unavailable ('{bad_model}') -> advancing to '{new_model}' (Key #{best_idx + 1}).")
            self._client = self._get_client_for_key(self.current_key_index)
            return self._client

        # 1.1 Unauthenticated or Corrupted Key (401)
        if any(w in err_lower for w in ["401", "unauthenticated", "access_token_type_unsupported"]):
            bad_key_idx = self.current_key_index
            logger.error(f"Permanent Auth Error on Key #{bad_key_idx + 1}. Quarantining key.")
            dead_duration = float(getattr(config, "GEMINI_DEAD_KEY_COOLDOWN", 315360000))
            for m in self.models:
                self.exhausted_keys[(bad_key_idx, m)] = now + dead_duration
            if self.db and bad_key_idx < len(self.keys):
                dead_info = {"error": err_str, "cooldown_duration": int(dead_duration)}
                await self.db.save_key_meta(self.keys[bad_key_idx], "gemini", status="exhausted", exhausted_at=int(now), raw_info_json=json.dumps(dead_info))

            best_idx, new_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
            self.current_key_index = best_idx
            if new_model in self.models:
                self.current_model_index = self.models.index(new_model)
            self._client = self._get_client_for_key(self.current_key_index)
            return self._client

        # 1.2 Geo-Blocking / Region Restriction (403)
        if any(w in err_lower for w in ["user location is not supported", "location is not supported", "country is not supported", "403 forbidden"]):
            self.consecutive_geo_blocked_count += 1
            geo_msg = f"⚠️ Gemini API Region Notice ({self.consecutive_geo_blocked_count}/3): User location not supported."
            logger.warning(geo_msg)
            if hasattr(proxy_rotator, "mark_proxy_geo_blocked") and hasattr(proxy_rotator, "get_healthy_proxy"):
                current_p = proxy_rotator.get_healthy_proxy()
                if current_p:
                    proxy_rotator.mark_proxy_geo_blocked(current_p)
                next_p = proxy_rotator.get_healthy_proxy()
                if next_p:
                    logger.info(f"Switching to next healthy proxy: '{next_p}'")
                    self._clients_cache.clear()
                    self._client = self._get_client_for_key(self.current_key_index)
                    return self._client

        # 2. Server Overload (500/502/503/504) -> Instant Model Outage Penalty, ZERO Sleep Loops
        if any(w in err_lower for w in ["503", "overloaded", "high demand", "unavailable", "500", "502", "504"]):
            self.consecutive_503_count += 1
            prev_model = self.get_model()

            quarantine_time = min(60.0, 15.0 * (1.3 ** min(self.consecutive_503_count - 1, 4)))
            self.model_congestion[prev_model] = now + quarantine_time

            best_idx, new_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
            self.current_key_index = best_idx
            if new_model in self.models:
                self.current_model_index = self.models.index(new_model)

            warn_msg = (
                f"⚡ Google 503 Server Capacity Outage on '{prev_model}'. "
                f"Model penalized {quarantine_time:.0f}s. Instant failover -> '{new_model}' (Key #{best_idx + 1})."
            )
            logger.warning(warn_msg)
            self._client = self._get_client_for_key(self.current_key_index)
            return self._client

        # 3. 429 Rate Limit Parsing
        cooldown_sec, is_daily, quota_meta = parse_gemini_error_cooldown(error_msg_or_ex)
        prev_key_idx = self.current_key_index
        curr_model = self.get_model()

        target_model_for_meta = quota_meta.get("target_model") or curr_model
        limit_rec = {
            "model_name": target_model_for_meta,
            "quota_metric": quota_meta.get("quota_metric"),
            "quota_value": quota_meta.get("quota_value"),
            "retry_delay_sec": quota_meta.get("retry_delay_sec"),
            "is_daily": is_daily
        }
        self.discovered_model_limits[target_model_for_meta] = limit_rec

        if self.db and hasattr(self.db, "record_model_quota_limit"):
            asyncio.create_task(self.db.record_model_quota_limit(
                model_name=target_model_for_meta,
                quota_metric=quota_meta.get("quota_metric"),
                quota_value=quota_meta.get("quota_value"),
                retry_delay_sec=quota_meta.get("retry_delay_sec"),
                is_daily=is_daily
            ))

        # 3.1 Daily Quota Exceeded (RPD CAP)
        if is_daily:
            logger.warning(f"🛑 Daily quota cap reached for model '{curr_model}' on Key #{prev_key_idx + 1}.")
            self.exhausted_keys[(prev_key_idx, curr_model)] = now + float(get_seconds_until_pacific_midnight())
            self.daily_request_counts[(prev_key_idx, curr_model)] = 99999

            if self.db and prev_key_idx < len(self.keys):
                exhaust_info = {"error": err_str, "cooldown_duration": int(cooldown_sec), "is_daily": True}
                await self.db.save_key_meta(self.keys[prev_key_idx], "gemini", status="exhausted", exhausted_at=int(now), raw_info_json=json.dumps(exhaust_info))

            best_idx, new_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
            self.current_key_index = best_idx
            if new_model in self.models:
                self.current_model_index = self.models.index(new_model)
            
            logger.info(f"🔄 Switched after daily cap: Key #{best_idx + 1}, Model '{new_model}'.")
            self._client = self._get_client_for_key(self.current_key_index)
            return self._client

        # 3.2 429 RPM/TPM Rate Limit: Non-Blocking Instant Key Switch
        self.exhausted_keys[(prev_key_idx, curr_model)] = now + cooldown_sec
        exact_delay = quota_meta.get("retry_delay_sec")

        if self.db and prev_key_idx < len(self.keys):
            exhaust_info = {"error": err_str, "cooldown_duration": int(cooldown_sec), "is_daily": False}
            await self.db.save_key_meta(self.keys[prev_key_idx], "gemini", status="exhausted", exhausted_at=int(now), raw_info_json=json.dumps(exhaust_info))

        acc_id = self.get_account_for_key(prev_key_idx)
        proj_id = self.get_project_for_key(prev_key_idx)

        logger.warning(
            f"⚡ Key #{prev_key_idx + 1}/{len(self.keys)} (Acc #{acc_id+1}, Proj #{proj_id+1}) "
            f"cooldown ({cooldown_sec:.1f}s) on '{curr_model}'. Server retryDelay: {exact_delay}s."
        )

        best_idx, new_model = self.select_best_key_and_model(estimated_tokens=estimated_tokens)
        self.current_key_index = best_idx
        if new_model in self.models:
            self.current_model_index = self.models.index(new_model)

        if self.db:
            try:
                await self.db.set_memory("gemini_working_key_index", str(self.current_key_index))
                await self.db.set_memory("gemini_working_model_index", str(self.current_model_index))
            except Exception:
                pass

        self._client = self._get_client_for_key(self.current_key_index)
        return self._client

    async def handle_error_exhausted(self, error_msg: str):
        """Marks the current key as exhausted with dynamic cooldown without full switch."""
        err_lower = str(error_msg).lower()
        if "user location is not supported" in err_lower or ("location" in err_lower and "not supported" in err_lower):
            logger.warning("handle_error_exhausted skipped: error is location geo-block, not key exhaustion.")
            return
        if any(k in err_lower for k in ["cannot connect", "connection refused", "clientconnectorerror", "ssl", "proxy"]):
            logger.warning("handle_error_exhausted skipped: error is network/proxy failure, not key exhaustion.")
            return

        cooldown_sec, is_daily, quota_meta = parse_gemini_error_cooldown(error_msg)
        now = time.time()
        curr_m = self.get_model()
        self.exhausted_keys[(self.current_key_index, curr_m)] = now + cooldown_sec

        if self.db and self.keys:
            active_key = self.keys[self.current_key_index]
            raw_info = {"cooldown_duration": int(cooldown_sec), "is_daily": is_daily, "meta": quota_meta}
            await self.db.save_key_meta(active_key, "gemini", status="exhausted", exhausted_at=int(now), raw_info_json=json.dumps(raw_info))

    async def mark_key_exhausted(self, error_msg: str = None):
        """Marks the current key as exhausted/invalid in memory and SQLite."""
        now = time.time()
        cooldown = float(getattr(config, "GEMINI_DEAD_KEY_COOLDOWN", 315360000))
        for m in self.models:
            self.exhausted_keys[(self.current_key_index, m)] = now + cooldown
        if self.db and self.keys:
            active_key = self.keys[self.current_key_index]
            raw_info = {"error": error_msg, "cooldown_duration": int(cooldown)} if error_msg else None
            await self.db.save_key_meta(active_key, "gemini", status="exhausted", exhausted_at=int(now), raw_info_json=json.dumps(raw_info) if raw_info else None)

    async def rotate_key_async(self, error_msg: str = None) -> genai.Client:
        """Cascades model/key pool dynamically and non-blockingly."""
        if error_msg:
            return await self.handle_quota_exhausted(error_msg)

        if len(self.keys) > 1:
            self.current_key_index = (self.current_key_index + 1) % len(self.keys)
        elif len(self.models) > 1:
            self.rotate_to_next_model(reason="Manual rotation")

        if self.db:
            try:
                await self.db.set_memory("gemini_working_key_index", str(self.current_key_index))
                await self.db.set_memory("gemini_working_model_index", str(self.current_model_index))
            except Exception as e:
                logger.error(f"Error saving Gemini rotation indexes: {str(e)}")

        self._client = self._get_client_for_key(self.current_key_index)
        return self._client


class PollinationsKeyManager:
    def __init__(self, db_manager=None):
        self.keys = getattr(config, "POLLINATIONS_KEYS", [])
        self.current_index = 0
        self.db = db_manager

    async def load_saved_index(self):
        """Imports Pollinations keys into the DB, asynchronously requesting owners via API."""
        if self.db:
            try:
                for key in self.keys:
                    meta = await self.db.get_key_meta(key)
                    if not meta:
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
        timeout_val = getattr(config, "KEY_INFO_TIMEOUT", 10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout_val) as client_httpx:
                resp = await client_httpx.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    owner = data.get("owner") or data.get("user_id") or f"user_{key[:10]}"
                    key_type = data.get("type") or ("secret" if key.startswith("sk_") else "publishable")
                    return owner, key_type, data
        except Exception as e:
            logger.debug(f"Failed to get Pollinations key metadata via API: {str(e)}")

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
        rec_time = getattr(config, "POLLINATIONS_KEY_RECOVERY_TIME", 3600)
        for i in range(len(self.keys)):
            idx = (self.current_index + i) % len(self.keys)
            key = self.keys[idx]
            meta = await self.db.get_key_meta(key) if self.db else None

            if meta and meta.get("status") == "exhausted":
                ex_at = meta.get("exhausted_at") or 0
                if (now - ex_at) >= rec_time:
                    logger.info(f"Pollinations key limits {key[:10]}... updated. Resetting to active.")
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
        await self.mark_current_key_exhausted()

        if not self.keys or len(self.keys) <= 1:
            return self.get_key()

        self.current_index = (self.current_index + 1) % len(self.keys)

        if self.db:
            try:
                await self.db.set_memory("pollinations_working_key_index", str(self.current_index))
            except Exception as e:
                logger.error(f"Error saving Pollinations index: {str(e)}")

        return await self.get_active_key()


KeyManager = GeminiKeyManager
