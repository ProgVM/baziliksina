# utils.py
import json
import logging
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger("Utils")

class TelegramJSONEncoder(json.JSONEncoder):
    """A custom JSON encoder that converts any Telegram data types into a serializable format."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.hex()
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, Path):
            return str(obj.resolve())
        if hasattr(obj, "to_dict"):
            try:
                return obj.to_dict()
            except Exception:
                pass
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return super().default(obj)


def safe_serialize(obj) -> str:
    """Safely serializes complex objects and dictionaries into a JSON string."""
    try:
        return json.dumps(obj, cls=TelegramJSONEncoder, ensure_ascii=False)
    except Exception as e:
        logger.error(f"JSON serialization error: {str(e)}")
        return "{}"


def safe_deserialize(json_str: str) -> dict:
    """Safely decodes a JSON string into a dictionary."""
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except Exception:
        return {}


def sanitize_filename(name: str) -> str:
    """Sanitizes a string for use as a safe filename on disk."""
    import re
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.replace(" ", "_")[:100]
