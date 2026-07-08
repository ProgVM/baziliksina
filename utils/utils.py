# utils.py
import json
import logging
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger("Utils")

def matches_filter(val: str, whitelist: list, blacklist: list, default_allow: bool = True) -> bool:
    """
    Universal checker for whitelists and blacklists supporting wildcards/keywords.
    Keywords: 'all', 'any', '*', 'none', 'nothing', 'empty', 'null'.
    """
    import re
    val_str = str(val).strip()
    if not val_str:
        return default_allow

    # Normalize lists and strip whitespace
    w_list = [str(w).strip().lower() for w in whitelist if str(w).strip()] if whitelist else []
    b_list = [str(b).strip().lower() for b in blacklist if str(b).strip()] if blacklist else []

    if not w_list and not b_list:
        return True

    # 1. Evaluate Blacklist first (if set)
    if b_list:
        if any(b in ["all", "any", "*"] for b in b_list):
            return False  # Everything is blocked
            
        b_active = [b for b in b_list if b not in ["none", "nothing", "empty", "null"]]
        if b_active:
            for pattern in b_active:
                try:
                    if re.search(pattern, val_str, re.IGNORECASE):
                        return False
                except Exception:
                    if pattern in val_str.lower():
                        return False

    # 2. Evaluate Whitelist
    if w_list:
        if any(w in ["all", "any", "*"] for w in w_list):
            return True  # Everything is allowed
            
        if all(w in ["none", "nothing", "empty", "null"] for w in w_list):
            return False  # Block everything
            
        w_active = [w for w in w_list if w not in ["none", "nothing", "empty", "null"]]
        if w_active:
            matched = False
            for pattern in w_active:
                try:
                    if re.search(pattern, val_str, re.IGNORECASE):
                        matched = True
                        break
                except Exception:
                    if pattern in val_str.lower():
                        matched = True
                        break
            if not matched:
                return False

    return True

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

async def wait_for_google_file_active(gemini_client, file_name: str, timeout_seconds: int = None) -> bool:
    """
    Periodically queries the Google Files API to wait until the specified file 
    transitions from 'PROCESSING' to 'ACTIVE' status. Returns True if successful.
    """
    import asyncio
    import logging
    from config import GOOGLE_UPLOAD_TIMEOUT
    
    if timeout_seconds is None:
        timeout_seconds = int(GOOGLE_UPLOAD_TIMEOUT) if GOOGLE_UPLOAD_TIMEOUT else 30
        
    log = logging.getLogger("Utils")
    attempts = 0
    try:
        file_info = await gemini_client.aio.files.get(name=file_name)
        while file_info.state.name == "PROCESSING" and attempts < timeout_seconds:
            log.info(f"File '{file_info.display_name}' is still processing in Google cloud. Waiting... ({attempts+1}/{timeout_seconds})")
            await asyncio.sleep(1.0)
            file_info = await gemini_client.aio.files.get(name=file_name)
            attempts += 1
        if file_info.state.name == "ACTIVE":
            return True
        log.warning(f"Google file processing finished with state: {file_info.state.name}")
    except Exception as e:
        log.error(f"Error while waiting for Google file state: {str(e)}")
    return False


def safe_telegram_html(text: str) -> str:
    """
    Safely escapes characters (like &, <, >) inside text to be compatible with 
    Telegram HTML parse mode, while preserving valid, supported Telegram HTML tags
    (including <b>, <i>, <u>, <s>, <tg-spoiler>, <blockquote expandable>, <sub>, <sup>, and <mark>).
    """
    import re
    
    # List of allowed tag names in Telegram HTML format (as of 2026)
    allowed_tags = [
        'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 
        'span', 'tg-spoiler', 'a', 'tg-emoji', 'code', 'pre', 'blockquote',
        'details', 'summary', 'sub', 'sup', 'mark', 'time'
    ]
    
    tag_pattern = re.compile(r'<(/?)(\\w+)([^>]*)>')
    
    parts = []
    last_idx = 0
    
    for match in tag_pattern.finditer(text):
        start, end = match.span()
        # Escaping non-tag segments
        before_text = text[last_idx:start]
        before_escaped = before_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        parts.append(before_escaped)
        
        is_closing = match.group(1) == '/'
        tag_name = match.group(2).lower()
        attrs = match.group(3)
        
        if tag_name in allowed_tags:
            # Let the valid tag pass through intact
            parts.append(match.group(0))
        else:
            # Escape invalid tags entirely
            tag_escaped = match.group(0).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            parts.append(tag_escaped)
            
        last_idx = end
        
    after_text = text[last_idx:]
    after_escaped = after_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    parts.append(after_escaped)
    
    return ''.join(parts)


async def should_process_message_event(event, me, action_type="save", db=None) -> bool:
    """
    Evaluates whether an incoming or outgoing message event should be processed
    (either 'save' to DB or 'generate' / trigger AI response) based on config.
    """
    import re
    import config
    from parser import get_media_type_description

    if not event or not event.message:
        return False

    is_outgoing = event.sender_id == me.id

    # 1. Base Save / Trigger Flags Checks
    if action_type == "save":
        if is_outgoing:
            if not config.SAVE_OUTGOING_NEW_MESSAGES:
                logger.info(f"[Message {event.message.id}] Skipping save: SAVE_OUTGOING_NEW_MESSAGES is disabled.")
                return False
        else:
            if not config.SAVE_INCOMING_MESSAGES:
                logger.info(f"[Message {event.message.id}] Skipping save: SAVE_INCOMING_MESSAGES is disabled.")
                return False
    elif action_type == "trigger":
        if is_outgoing:
            if not config.TRIGGER_ON_OUTGOING_NEW_MESSAGES:
                logger.info(f"[Message {event.message.id}] Skipping trigger: TRIGGER_ON_OUTGOING_NEW_MESSAGES is disabled.")
                return False
        else:
            if not config.TRIGGER_ON_INCOMING:
                logger.info(f"[Message {event.message.id}] Skipping trigger: TRIGGER_ON_INCOMING is disabled.")
                return False

        # 2. Check Allowed Message Types (for both incoming and outgoing triggers)
        m_type_raw = get_media_type_description(event.message) or "text"
        m_type_lower = m_type_raw.lower()
        
        # Normalize media types for seamless config filtering
        if "voice" in m_type_lower:
            m_type_norm = "voice"
        elif "video note" in m_type_lower or m_type_lower == "video note":
            m_type_norm = "video"
        elif "file" in m_type_lower or m_type_lower == "document":
            m_type_norm = "document"
        elif "todo" in m_type_lower or m_type_lower == "list":
            m_type_norm = "list"
        else:
            m_type_norm = m_type_lower

        allowed_types_lower = [t.lower().strip() for t in config.ALLOWED_MESSAGE_TYPES]
        if m_type_norm not in allowed_types_lower:
            logger.info(f"[Message {event.message.id}] Skipping: Media type '{m_type_raw}' (normalized to '{m_type_norm}') is not allowed in ALLOWED_MESSAGE_TYPES.")
            return False

    # 3. Check Chat Whitelist / Blacklist Restrictions
    chat_id = int(event.chat_id)
    if config.CHAT_BLACKLIST and chat_id in config.CHAT_BLACKLIST:
        logger.info(f"[Message {event.message.id}] Skipping: Chat {chat_id} is in CHAT_BLACKLIST.")
        return False
    if config.CHAT_WHITELIST and chat_id not in config.CHAT_WHITELIST:
        logger.info(f"[Message {event.message.id}] Skipping: Chat {chat_id} is not in CHAT_WHITELIST.")
        return False

    # 4. Check Message Whitelist / Blacklist (Regex or Raw Values)
    text_content = event.message.message or ""

    whitelist = config.MSG_SAVE_WHITELIST if action_type == "save" else config.MSG_GEN_WHITELIST
    blacklist = config.MSG_SAVE_BLACKLIST if action_type == "save" else config.MSG_GEN_BLACKLIST

    if not matches_filter(text_content, whitelist, blacklist, default_allow=True):
        logger.info(f"[Message {event.message.id}] Skipping: Message text matches blacklist or doesn't match whitelist.")
        return False

    if event.message.media:
        file_id = "unknown"
        if hasattr(event.message.media, "document") and event.message.media.document:
            file_id = str(event.message.media.document.id)
        elif hasattr(event.message.media, "photo") and event.message.media.photo:
            file_id = str(event.message.media.photo.id)
        if not matches_filter(file_id, config.INCOMING_FILE_WHITELIST, config.INCOMING_FILE_BLACKLIST):
            logger.info(f"[Message {event.message.id}] Skipping: File attachment ID {file_id} matches blacklist or doesn't match whitelist.")
            return False

    # 5. Additional Generation Triggers check
    if action_type == "trigger" and not is_outgoing:
        is_private = event.is_private
        is_group = event.is_group or (event.is_channel and getattr(event.chat, 'megagroup', False))
        is_channel = event.is_channel and not getattr(event.chat, 'megagroup', False)

        # Match AI_RESPONSE_MODE
        if config.AI_RESPONSE_MODE == "private_only" and not is_private:
            logger.info(f"[Message {event.message.id}] Skipping trigger: Private-only mode, but chat is not private.")
            return False
        elif config.AI_RESPONSE_MODE == "group_only" and not is_group:
            logger.info(f"[Message {event.message.id}] Skipping trigger: Group-only mode, but chat is not a group.")
            return False
        elif config.AI_RESPONSE_MODE == "channel_only" and not is_channel:
            logger.info(f"[Message {event.message.id}] Skipping trigger: Channel-only mode, but chat is not a channel.")
            return False

        # Match AI_RESPONSE_TRIGGERS
        if is_private:
            triggered = True
        else:
            triggered = False
            text_lower = text_content.lower()
            if "name" in config.AI_RESPONSE_TRIGGERS:
                me_name = (me.first_name or "").lower()
                if me_name and me_name in text_lower:
                    triggered = True
            if "username" in config.AI_RESPONSE_TRIGGERS and me.username:
                if f"@{me.username.lower()}" in text_lower:
                    triggered = True
            if "mentioned" in config.AI_RESPONSE_TRIGGERS and event.mentioned:
                triggered = True
            if "reply_to_me" in config.AI_RESPONSE_TRIGGERS and event.message.is_reply:
                # Strictly verify if the replied-to message belongs to the model (the userbot)
                if event.message.reply_to and db:
                    reply_to_msg_id = event.message.reply_to.reply_to_msg_id
                    async with db.db.execute(
                        "SELECT role FROM messages WHERE chat_id = ? AND msg_id = ? LIMIT 1",
                        (str(event.chat_id), reply_to_msg_id)
                    ) as cursor:
                        row = await cursor.fetchone()
                    if row and row[0] == "model":
                        triggered = True
                else:
                    triggered = True
            if config.AI_RESPONSE_TRIGGERS and not triggered:
                logger.info(f"[Message {event.message.id}] Skipping trigger: Message did not fire any of the active triggers: {config.AI_RESPONSE_TRIGGERS}")
                return False

    return True


def should_process_reaction_event(emoji_or_id: str, actor_id: int, bot_id: int, is_add: bool) -> bool:
    """
    Evaluates whether a reaction update event should be logged or triggered
    based on active whitelists, blacklists, and save/trigger settings.
    """
    import config
    is_outgoing = actor_id == bot_id

    # 1. Base Save / Trigger Flags
    if is_outgoing:
        if is_add and not config.SAVE_OUTGOING_REACTION_ADD: return False
        if not is_add and not config.SAVE_OUTGOING_REACTION_REMOVE: return False
    else:
        if is_add and not config.SAVE_INCOMING_REACTION_ADD: return False
        if not is_add and not config.SAVE_INCOMING_REACTION_REMOVE: return False

    # 2. Match Reaction Whitelist / Blacklist
    if config.REACTION_BLACKLIST and emoji_or_id in config.REACTION_BLACKLIST: return False
    if config.REACTION_WHITELIST and emoji_or_id not in config.REACTION_WHITELIST: return False

    return True

def load_feedback_template(section_name: str, default_text: str) -> str:
    """
    Loads a specific notification section from config/feedback_prompt.txt dynamically.
    """
    import config
    from pathlib import Path
    import re
    path = config.BASE_DIR / "config" / "feedback_prompt.txt"
    if not path.exists():
        return default_text

async def should_send_read_acknowledge(message_or_event, me, db=None, is_trigger_fired: bool = False) -> bool:
    """
    Dynamically checks whether an incoming message should be marked as read
    based on granular white/black lists inside configuration parameters.
    Supports flexible wildcards, trigger states, message types, and peer classes.
    """
    import config
    from parser import get_media_type_description
    from telethon.tl import types as tl_types
    
    msg = message_or_event.message if hasattr(message_or_event, "message") else message_or_event
    if not msg:
        return False
        
    if msg.sender_id == me.id:
        return False

    # 1. Standardize message type
    m_type = (get_media_type_description(msg) or "text").lower()
    if "voice" in m_type:
        m_type_norm = "voice"
    elif "video note" in m_type:
        m_type_norm = "video"
    elif "file" in m_type or "document" in m_type:
        m_type_norm = "document"
    elif "todo" in m_type or "list" in m_type:
        m_type_norm = "list"
    else:
        m_type_norm = m_type

    # 2. Standardize peer type
    u_low = str(message_or_event.chat_id)
    is_private = getattr(message_or_event, 'is_private', False) or (msg.is_private if hasattr(msg, 'is_private') else isinstance(msg.peer_id, tl_types.PeerUser))
    is_group = getattr(message_or_event, 'is_group', False) or (msg.is_group if hasattr(msg, 'is_group') else isinstance(msg.peer_id, tl_types.PeerChat))
    is_channel = getattr(message_or_event, 'is_channel', False) or (msg.is_channel if hasattr(msg, 'is_channel') else isinstance(msg.peer_id, tl_types.PeerChannel))
    
    peer_type = "private" if is_private else ("group" if is_group else "channel")

    # 3. Resolve metadata details
    sender_id = str(msg.sender_id) if msg.sender_id else ""
    chat_id = str(msg.chat_id) if msg.chat_id else ""
    
    sender_username = ""
    chat_username = ""
    if hasattr(msg, "sender") and msg.sender and getattr(msg.sender, "username", None):
        sender_username = f"@{msg.sender.username.lower()}"
    if hasattr(message_or_event, "chat") and message_or_event.chat and getattr(message_or_event.chat, "username", None):
        chat_username = f"@{message_or_event.chat.username.lower()}"

    # 4. Standardize trigger details
    trigger_state = "trigger:none"
    if is_trigger_fired:
        trigger_state = "trigger:all"
        t_lower = (msg.message or "").lower()
        if me.first_name and me.first_name.lower() in t_lower:
            trigger_state = "trigger:name"
        elif me.username and f"@{me.username.lower()}" in t_lower:
            trigger_state = "trigger:username"
        elif getattr(message_or_event, "mentioned", False):
            trigger_type = "trigger:mentioned"
        elif msg.is_reply:
            trigger_state = "trigger:reply_to_me"

    def matches_rule(rule_item: str) -> bool:
        r = rule_item.strip().lower()
        if not r:
            return False
        if r in ["all", "any", "*"]:
            return True
        if r == "none":
            return False
        if r.startswith("peer:"):
            return peer_type == r.split(":", 1)[1]
        if r.startswith("type:"):
            return m_type_norm == r.split(":", 1)[1]
        if r.startswith("user:") or r.startswith("sender:"):
            u_val = r.split(":", 1)[1]
            return sender_id == u_val or (sender_username and u_val == sender_username)
        if r.startswith("chat:"):
            c_val = r.split(":", 1)[1]
            return chat_id == c_val or (chat_username and c_val == chat_username)
        if r.startswith("trigger:"):
            t_val = r.split(":", 1)[1]
            if t_val == "all": return is_trigger_fired
            if t_val == "none": return not is_trigger_fired
            return trigger_state == f"trigger:{t_val}"
        if r.replace("-", "").isdigit():
            return r in [sender_id, chat_id]
        if r.startswith("@"):
            return r in [sender_username, chat_username]
        return False

    if config.READ_ACK_BLACKLIST:
        for b_rule in config.READ_ACK_BLACKLIST:
            if matches_rule(b_rule):
                return False

    if config.READ_ACK_WHITELIST:
        for w_rule in config.READ_ACK_WHITELIST:
            if matches_rule(w_rule):
                return True
        return False

    return True
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'\[{}\]\s*\n(.*?)(?=\n\[|\Z)'.format(re.escape(section_name)), content, re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return default_text