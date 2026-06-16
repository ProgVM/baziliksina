# db_manager.py
import json
import sqlite3
import aiosqlite
import logging
import base64
import time
from datetime import datetime, date
from google.genai import types
from config import SAFE_DB_DIR, MESSAGES_LIMIT, SUMMARIZATION_KEEP_LIMIT, CONTEXT_LOCAL_RATIO, CONTEXT_LOCAL_MIN_LIMIT

logger = logging.getLogger("Database")
DB_PATH = SAFE_DB_DIR / "bot_context.db"

def clean_for_json(obj):
    """Recursively cleans data and converts non-serializable types (bytes, datetime) into a JSON-compatible format."""
    if isinstance(obj, dict):
        if "inline_data" in obj:
            if isinstance(obj["inline_data"], dict):
                obj["inline_data"]["data"] = None
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    elif isinstance(obj, bytes):
        return base64.b64encode(obj).decode("utf-8")
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif hasattr(obj, "model_dump"):
        return clean_for_json(obj.model_dump())
    elif hasattr(obj, "__dict__"):
        return clean_for_json(obj.__dict__)
    return obj

def content_to_dict(content) -> dict:
    if hasattr(content, "model_dump"):
        data = content.model_dump()
    else:
        data = dict(content)
    for part in (data.get("parts") or []):
        if part.get("inline_data") and "data" in part["inline_data"]:
            part["inline_data"]["data"] = None
    return clean_for_json(data)

def dict_to_content(data: dict) -> types.Content:
    def restore_bytes(obj):
        if isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                if k in ["thought_signature", "thought"] and isinstance(v, str):
                    try:
                        new_dict[k] = base64.b64decode(v)
                    except Exception as b_err:
                        logger.error(f"Base64 decoding error for {k}: {str(b_err)}")
                        new_dict[k] = v
                else:
                    new_dict[k] = restore_bytes(v)
            return new_dict
        elif isinstance(obj, list):
            return [restore_bytes(v) for v in obj]
        return obj

    restored_data = restore_bytes(data)
    if "parts" not in restored_data or restored_data["parts"] is None:
        restored_data["parts"] = []
    return types.Content.model_validate(restored_data)


class DBManager:
    def __init__(self):
        self.db = None

    async def connect(self):
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA foreign_keys=ON;")
        await self._init_db()
        logger.info(f"SQLite DB connection successfully established. File: {DB_PATH}")

    async def _init_db(self):
        async with self.db.cursor() as cursor:
            # 1. Main message history table (cleared of secondary fields)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT,
                    raw_content_json TEXT DEFAULT NULL,
                    media_info TEXT DEFAULT NULL,
                    msg_id INTEGER DEFAULT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Secondary message metadata (backgrounds, colors, patterns, gifts, buttons)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS msgs_meta (
                    chat_id TEXT NOT NULL,
                    msg_id INTEGER NOT NULL,
                    meta_text TEXT DEFAULT NULL,
                    raw_meta_json TEXT DEFAULT NULL,
                    PRIMARY KEY (chat_id, msg_id)
                )
            """)

            # 3. Summaries
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    chat_id TEXT PRIMARY KEY,
                    summary TEXT
                )
            """)

            # 4. Shared end-to-end AI memory
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS shared_memory (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # 5. Timers
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS timers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    execute_at INTEGER NOT NULL,
                    action_description TEXT NOT NULL,
                    code_to_execute TEXT DEFAULT NULL
                )
            """)

            # 6. Auto-wake triggers
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    action_description TEXT NOT NULL,
                    code_to_execute TEXT DEFAULT NULL
                )
            """)

            # 7. Detailed user metadata with a flexible raw_meta_json field
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS users_meta (
                    id TEXT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT DEFAULT NULL,
                    bio TEXT DEFAULT NULL,
                    premium INTEGER DEFAULT 0,
                    verified INTEGER DEFAULT 0,
                    scam INTEGER DEFAULT 0,
                    fake INTEGER DEFAULT 0,
                    birthday TEXT DEFAULT NULL,
                    emoji_status_id TEXT DEFAULT NULL,
                    avatar_path TEXT DEFAULT NULL,
                    raw_meta_json TEXT DEFAULT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 8. Detailed metadata of groups/channels with a flexible raw_meta_json field
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats_meta (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    username TEXT DEFAULT NULL,
                    type TEXT NOT NULL,
                    bio TEXT DEFAULT NULL,
                    description TEXT DEFAULT NULL,
                    photo_path TEXT DEFAULT NULL,
                    linked_chat_id TEXT DEFAULT NULL,
                    raw_meta_json TEXT DEFAULT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 9. Tracking states, limits, quotas, and API key owners
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_value TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,         -- 'gemini' or 'pollinations'
                    key_type TEXT DEFAULT NULL,     -- 'secret' or 'publishable' (for pollinations)
                    owner TEXT DEFAULT NULL,        -- account owner (for pollinations)
                    status TEXT DEFAULT 'active',   -- 'active' or 'exhausted'
                    exhausted_at INTEGER DEFAULT NULL, -- quota exhaustion timestamp
                    last_used_at INTEGER DEFAULT NULL,  -- last used timestamp
                    models_json TEXT DEFAULT NULL,   -- list of supported models (for gemini)
                    raw_info_json TEXT DEFAULT NULL  -- extended metadata (JSON)
                )
            """)

            # 10. Storage of code and JSON schemas of dynamic custom AI tools
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_tools (
                    name TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    description TEXT DEFAULT NULL,
                    code TEXT NOT NULL,
                    parameters_schema TEXT DEFAULT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # --- INDEXES FOR ULTRA-HIGH QUERY OPTIMIZATION ---
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, msg_id)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_meta_username ON users_meta(username)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_meta_username ON chats_meta(username)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_timers_execute ON timers(execute_at)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys(provider)")

        await self.db.commit()

    async def save_message(self, chat_id: str, role: str, text: str = None, media_info: str = None, msg_id: int = None, content_obj: types.Content = None):
        if content_obj:
            raw_json = json.dumps(content_to_dict(content_obj))
            role = content_obj.role
            if not text:
                text_parts = [p.text for p in content_obj.parts if p.text]
                text = "\n".join(text_parts) if text_parts else None
        else:
            parts = []
            if text:
                parts.append(types.Part.from_text(text=text))
            new_content = types.Content(role=role, parts=parts)
            raw_json = json.dumps(content_to_dict(new_content))

        await self.db.execute("""
            INSERT INTO messages (chat_id, role, text, raw_content_json, media_info, msg_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(chat_id), role, text, raw_json, media_info, msg_id))
        await self.db.commit()

    async def save_msg_meta(self, chat_id: str, msg_id: int, meta_text: str = None, raw_meta_dict: dict = None):
        """Saves accompanying visual/structural information about the message to the msgs_meta table."""
        raw_json = json.dumps(clean_for_json(raw_meta_dict)) if raw_meta_dict else None
        await self.db.execute("""
            INSERT INTO msgs_meta (chat_id, msg_id, meta_text, raw_meta_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, msg_id) DO UPDATE SET
                meta_text = excluded.meta_text,
                raw_meta_json = excluded.raw_meta_json
        """, (str(chat_id), int(msg_id), meta_text, raw_json))
        await self.db.commit()

    async def get_msg_meta(self, chat_id: str, msg_id: int) -> dict:
        """Retrieves visual metadata of a specific message."""
        async with self.db.execute("SELECT meta_text, raw_meta_json FROM msgs_meta WHERE chat_id = ? AND msg_id = ?", (str(chat_id), int(msg_id))) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "meta_text": row[0],
                    "raw_meta": json.loads(row[1]) if row[1] else {}
                }
            return None

    async def update_message_text(self, chat_id: str, msg_id: int, new_text: str, new_media_info: str = None):
        await self.db.execute(
            "UPDATE messages SET text = ?, media_info = ? WHERE chat_id = ? AND msg_id = ?",
            (new_text, new_media_info, str(chat_id), int(msg_id))
        )
        await self.db.commit()

    async def get_history(self, chat_id: str, limit: int = MESSAGES_LIMIT) -> list:
        """
        Extracts end-to-end message history.
        Ensures the active chat context is preserved while blending global cross-chat memory.
        """
        async with self.db.execute("SELECT summary FROM summaries WHERE chat_id = 'global'") as cursor:
            row = await cursor.fetchone()
        
        history = []
        if row and row[0]:
            summary_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"[System summary of past global events in all chats]: {row[0]}")]
            )
            history.append((summary_content, None))
            
            ack_content = types.Content(
                role="model",
                parts=[types.Part.from_text(text="Understood the end-to-end context of previous conversations with everyone. Continuing communication.")]
            )
            history.append((ack_content, None))

        # Dynamically allocate limits: active chat gets a configurable ratio of the total limit (guaranteed minimum)
        local_limit = max(CONTEXT_LOCAL_MIN_LIMIT, int(limit * CONTEXT_LOCAL_RATIO))
        global_limit = limit - local_limit

        # 1. Fetch recent messages specifically from the active chat
        async with self.db.execute("""
            SELECT m.role, m.text, m.raw_content_json, m.media_info, meta.meta_text, m.id, m.chat_id
            FROM messages m
            LEFT JOIN msgs_meta meta ON m.chat_id = meta.chat_id AND m.msg_id = meta.msg_id
            WHERE m.chat_id = ?
            ORDER BY m.id DESC LIMIT ?
        """, (str(chat_id), local_limit)) as cursor:
            local_rows = await cursor.fetchall()

        # 2. Fetch recent messages globally to maintain cross-chat memory
        async with self.db.execute("""
            SELECT m.role, m.text, m.raw_content_json, m.media_info, meta.meta_text, m.id, m.chat_id
            FROM messages m
            LEFT JOIN msgs_meta meta ON m.chat_id = meta.chat_id AND m.msg_id = meta.msg_id
            ORDER BY m.id DESC LIMIT ?
        """, (global_limit,)) as cursor:
            global_rows = await cursor.fetchall()

        # Merge results to eliminate duplicates and preserve chronological database order
        merged_rows_dict = {}
        for row in (local_rows + global_rows):
            m_db_id = row[5]  # m.id index
            merged_rows_dict[m_db_id] = row

        sorted_keys = sorted(merged_rows_dict.keys())
        sorted_rows = [merged_rows_dict[k] for k in sorted_keys]

        for role, text, raw_json, media_info, meta_text, msg_db_id, m_chat_id in sorted_rows:
            prefix = f"[Chat: {m_chat_id} | Message ID: {msg_db_id or 'unknown'}]\n"
            if meta_text:
                prefix += f"{meta_text}\n"

            full_text = f"{prefix}{text or ''}".strip()

            if raw_json:
                try:
                    content_obj = dict_to_content(json.loads(raw_json))
                    if content_obj.parts:
                        for part in content_obj.parts:
                            if part.text:
                                part.text = f"{prefix}{part.text}".strip()
                                break
                    history.append((content_obj, media_info))
                    continue
                except Exception as e:
                    logger.error(f"Failed to deserialize raw_content_json: {str(e)}")
            
            parts = []
            if full_text:
                parts.append(types.Part.from_text(text=full_text))
            fallback_content = types.Content(role=role, parts=parts)
            history.append((fallback_content, media_info))
            
        return history

    async def clear_history_for_summarization(self, chat_id: str, keep_last_n: int = SUMMARIZATION_KEEP_LIMIT):
        async with self.db.execute("""
            SELECT id FROM messages 
            ORDER BY id DESC LIMIT ?
        """, (keep_last_n,)) as cursor:
            rows = await cursor.fetchall()
            
        if not rows:
            return
        
        min_id_to_keep = rows[-1][0]
        await self.db.execute("DELETE FROM messages WHERE id < ?", (min_id_to_keep,))
        await self.db.commit()

    async def update_summary(self, chat_id: str, summary_text: str):
        await self.db.execute(
            "INSERT INTO summaries (chat_id, summary) VALUES ('global', ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET summary = excluded.summary",
            (summary_text,)
        )
        await self.db.commit()

    # --- GLOBAL MEMORY METHODS ---
    async def get_memory(self, key: str) -> str:
        async with self.db.execute("SELECT value FROM shared_memory WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_memory(self, key: str, value: str):
        await self.db.execute(
            "INSERT INTO shared_memory (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )
        await self.db.commit()

    # --- PERSISTENT TIMER METHODS ---
    async def add_timer(self, chat_id: str, delay_seconds: int, action_description: str, code_to_execute: str = None):
        execute_at = int(time.time()) + delay_seconds
        await self.db.execute(
            "INSERT INTO timers (chat_id, execute_at, action_description, code_to_execute) VALUES (?, ?, ?, ?)",
            (str(chat_id), execute_at, action_description, code_to_execute)
        )
        await self.db.commit()

    async def get_pending_timers(self) -> list:
        async with self.db.execute("SELECT id, chat_id, execute_at, action_description, code_to_execute FROM timers") as cursor:
            return await cursor.fetchall()

    async def delete_timer(self, timer_id: int):
        await self.db.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
        await self.db.commit()

    # --- WAKE TRIGGER METHODS ---
    async def add_trigger(self, chat_id: str, trigger_type: str, value: str, action_description: str, code_to_execute: str = None):
        await self.db.execute(
            "INSERT INTO triggers (chat_id, type, value, action_description, code_to_execute) VALUES (?, ?, ?, ?, ?)",
            (str(chat_id), trigger_type, value, action_description, code_to_execute)
        )
        await self.db.commit()

    async def get_active_triggers(self, chat_id: str) -> list:
        async with self.db.execute("SELECT id, type, value, action_description, code_to_execute FROM triggers WHERE chat_id = ?", (str(chat_id),)) as cursor:
            return await cursor.fetchall()

    async def delete_trigger(self, trigger_id: int):
        await self.db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        await self.db.commit()

    # --- PROFILE SAVING METHODS (WITH PASSIVE JSON EXTENSION) ---
    async def save_user_meta(self, user_id: str, meta_dict: dict):
        keys = ["id", "username", "first_name", "last_name", "phone", "bio", "premium", "verified", "scam", "fake", "birthday", "emoji_status_id", "avatar_path", "raw_meta_json"]
        vals = [meta_dict.get(k) for k in keys]
        
        idx_raw = keys.index("raw_meta_json")
        if isinstance(vals[idx_raw], dict):
            vals[idx_raw] = json.dumps(clean_for_json(vals[idx_raw]))

        placeholders = ", ".join(["?"] * len(keys))
        updates = ", ".join([f"{k} = excluded.{k}" for k in keys if k != "id"])
        
        sql = f"""
            INSERT INTO users_meta ({", ".join(keys)}) VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
        """
        await self.db.execute(sql, vals)
        await self.db.commit()

    async def get_user_meta(self, user_id: str) -> dict:
        async with self.db.execute("SELECT * FROM users_meta WHERE id = ?", (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            res = dict(zip(cols, row))
            if res.get("raw_meta_json"):
                try:
                    res["raw_meta"] = json.loads(res["raw_meta_json"])
                except Exception:
                    res["raw_meta"] = {}
            return res

    async def save_chat_meta(self, chat_id: str, meta_dict: dict):
        keys = ["id", "title", "username", "type", "bio", "description", "photo_path", "linked_chat_id", "raw_meta_json"]
        vals = [meta_dict.get(k) for k in keys]
        
        idx_raw = keys.index("raw_meta_json")
        if isinstance(vals[idx_raw], dict):
            vals[idx_raw] = json.dumps(clean_for_json(vals[idx_raw]))

        placeholders = ", ".join(["?"] * len(keys))
        updates = ", ".join([f"{k} = excluded.{k}" for k in keys if k != "id"])
        
        sql = f"""
            INSERT INTO chats_meta ({", ".join(keys)}) VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
        """
        await self.db.execute(sql, vals)
        await self.db.commit()

    async def get_chat_meta(self, chat_id: str) -> dict:
        async with self.db.execute("SELECT * FROM chats_meta WHERE id = ?", (str(chat_id),)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            res = dict(zip(cols, row))
            if res.get("raw_meta_json"):
                try:
                    res["raw_meta"] = json.loads(res["raw_meta_json"])
                except Exception:
                    res["raw_meta"] = {}
            return res

    async def save_key_meta(self, key_value: str, provider: str, key_type: str = None, owner: str = None, status: str = "active", exhausted_at: int = None, last_used_at: int = None, models_json: str = None, raw_info_json: str = None):
        """Saves or updates metadata of a specific API key in the database."""
        await self.db.execute("""
            INSERT INTO api_keys (key_value, provider, key_type, owner, status, exhausted_at, last_used_at, models_json, raw_info_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_value) DO UPDATE SET
                status = excluded.status,
                exhausted_at = excluded.exhausted_at,
                last_used_at = excluded.last_used_at,
                models_json = COALESCE(excluded.models_json, models_json),
                raw_info_json = COALESCE(excluded.raw_info_json, raw_info_json),
                owner = COALESCE(excluded.owner, owner),
                key_type = COALESCE(excluded.key_type, key_type)
        """, (key_value, provider, key_type, owner, status, exhausted_at, last_used_at, models_json, raw_info_json))
        await self.db.commit()

    async def get_key_meta(self, key_value: str) -> dict:
        """Returns information about a specific key from the database."""
        async with self.db.execute("SELECT * FROM api_keys WHERE key_value = ?", (key_value,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    async def get_keys_by_provider(self, provider: str) -> list:
        """Returns a list of all keys for the specified provider."""
        async with self.db.execute("SELECT * FROM api_keys WHERE provider = ?", (provider,)) as cursor:
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

    async def update_keys_status_by_owner(self, owner: str, status: str, exhausted_at: int = None):
        """Batch updates the limit status for all keys of a single owner (for Pollinations)."""
        await self.db.execute(
            "UPDATE api_keys SET status = ?, exhausted_at = ? WHERE owner = ?",
            (status, exhausted_at, owner)
        )
        await self.db.commit()

    # --- Managing custom dynamic AI tools in the DB ---
    async def save_custom_tool(self, name: str, category: str, description: str, code: str, parameters_schema: str = None):
        """Saves or updates a custom AI tool in the database."""
        await self.db.execute("""
            INSERT INTO custom_tools (name, category, description, code, parameters_schema)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                category = excluded.category,
                description = excluded.description,
                code = excluded.code,
                parameters_schema = COALESCE(excluded.parameters_schema, parameters_schema)
        """, (name, category, description, code, parameters_schema))
        await self.db.commit()

    async def get_custom_tool(self, name: str) -> dict:
        """Returns metadata and code of a specific custom tool."""
        async with self.db.execute("SELECT * FROM custom_tools WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    async def get_all_custom_tools(self) -> list:
        """Returns a list of all registered custom tools."""
        async with self.db.execute("SELECT * FROM custom_tools") as cursor:
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

    async def delete_custom_tool(self, name: str) -> bool:
        """Deletes a custom tool from the database. Returns True upon successful deletion."""
        async with self.db.execute("SELECT 1 FROM custom_tools WHERE name = ?", (name,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            return False
        await self.db.execute("DELETE FROM custom_tools WHERE name = ?", (name,))
        await self.db.commit()
        return True

    async def close(self):
        """Closes the active database connection."""
        if self.db:
            await self.db.close()
            logger.info("DB connection successfully closed.")
