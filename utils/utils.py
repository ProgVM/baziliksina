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


def markdown_to_telegram_html(text: str) -> str:
    """
    Comprehensive converter that translates arbitrary Markdown, MarkdownV2, and Rich Text
    formatting syntax into standard Telegram-compatible HTML tags.
    """
    if not text:
        return text

    import re

    # 1. Code blocks with optional language specifier: ```python\ncode```
    def _repl_code_block(m):
        lang = m.group(1).strip() if m.group(1) else ""
        code = m.group(2)
        if lang:
            return f'<pre><code class="language-{lang}">{code}</code></pre>'
        return f'<pre>{code}</pre>'

    text = re.sub(r'```([a-zA-Z0-9_+-]*)\n?(.*?)```', _repl_code_block, text, flags=re.DOTALL)

    # 2. Inline code: `code`
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)

    # 3. Spoilers: ||text||
    text = re.sub(r'\|\|(.*?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text, flags=re.DOTALL)

    # 4. Expandable / Collapsible blockquotes: **> text OR ||> text
    def _repl_expandable_blockquote(m):
        content = m.group(1).strip()
        return f'<blockquote expandable>{content}</blockquote>'

    text = re.sub(r'(?:^\*\*>\s*|^\|>\s*)([^\n]+(?:\n[^\n]+)*)', _repl_expandable_blockquote, text, flags=re.MULTILINE)

    # 5. Standard blockquotes: > text
    def _repl_blockquote(m):
        content = m.group(1).strip()
        return f'<blockquote>{content}</blockquote>'

    text = re.sub(r'(?:^>\s*)([^\n]+(?:\n[^\n]+)*)', _repl_blockquote, text, flags=re.MULTILINE)

    # 6. Markdown links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Custom emoji tags: ![emoji](tg://emoji?id=123) or <emoji id="123">
    text = re.sub(r'!\[([^\]]*)\]\(tg://emoji\?id=(\d+)\)', r'<tg-emoji emoji-id="\2">\1</tg-emoji>', text)
    text = re.sub(r'<emoji\s+id=["\']?(\d+)["\']?\s*>(.*?)</emoji>', r'<tg-emoji emoji-id="\1">\2</tg-emoji>', text)

    # 8. Bold text: **text** or __bold_text__
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # 9. Underline text: +text+
    text = re.sub(r'\+(.*?)\+', r'<u>\1</u>', text, flags=re.DOTALL)

    # 10. Italic text: *text* or _text_
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text, flags=re.DOTALL)
    text = re.sub(r'(?<!_)\_(?!\_)(.*?)(?<!_)\_(?!\_)', r'<i>\1</i>', text, flags=re.DOTALL)

    # 11. Strikethrough: ~~text~~ or ~text~
    text = re.sub(r'~~?(.*?)~~?', r'<s>\1</s>', text, flags=re.DOTALL)

    # 12. Highlighted / Marked text: ==text==
    text = re.sub(r'==(.*?)==', r'<mark>\1</mark>', text, flags=re.DOTALL)

    # 13. Superscript text: ^text^
    text = re.sub(r'\^([^\^]+)\^', r'<sup>\1</sup>', text)

    return text


def safe_telegram_html(text: str) -> str:
    """
    Safely converts Markdown syntax to Telegram HTML tags and sanitizes HTML entities,
    preserving all rich text elements supported by Telegram (bold, italic, underline,
    strikethrough, spoiler, tg-emoji, blockquote, expandable blockquote, details, sub, sup, mark).
    """
    import re

    # First convert Markdown to valid Telegram HTML tags
    text = markdown_to_telegram_html(text)

    # Supported Telegram HTML tags catalog
    allowed_tags = [
        'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 
        'span', 'tg-spoiler', 'a', 'tg-emoji', 'code', 'pre', 'blockquote',
        'details', 'summary', 'sub', 'sup', 'mark', 'time', 'aside', 'cite'
    ]
    
    tag_pattern = re.compile(r'<(/?)(\w+)([^>]*)>')
    
    parts = []
    last_idx = 0
    
    for match in tag_pattern.finditer(text):
        start, end = match.span()
        before_text = text[last_idx:start]
        before_escaped = before_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        parts.append(before_escaped)
        
        tag_name = match.group(2).lower()
        
        if tag_name in allowed_tags:
            parts.append(match.group(0))
        else:
            tag_escaped = match.group(0).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            parts.append(tag_escaped)
            
        last_idx = end
        
    after_text = text[last_idx:]
    after_escaped = after_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    parts.append(after_escaped)
    
    return ''.join(parts)


def matches_advanced_filter(message_or_event, me, whitelist: list, blacklist: list, is_trigger_fired: bool = False, default_allow: bool = True) -> bool:
    """
    An advanced, unified filter evaluation engine that processes structured prefix rules
    (peer:, type:, trigger:, user:, chat:, text:, caption:) and standard text regex matches.
    """
    import re
    from parser import get_media_type_description
    from telethon.tl import types as tl_types
    import config

    msg = message_or_event.message if hasattr(message_or_event, "message") else message_or_event
    if not msg:
        return default_allow

    if msg.sender_id == me.id and not whitelist and not blacklist:
        return default_allow

    # 1. Resolve text and caption states
    text_content = msg.message or ""
    is_caption = msg.media is not None
    
    # 2. Extract message type
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

    # 3. Standardize peer type
    is_private = getattr(message_or_event, 'is_private', False) or (msg.is_private if hasattr(msg, 'is_private') else isinstance(msg.peer_id, tl_types.PeerUser))
    is_group = getattr(message_or_event, 'is_group', False) or (msg.is_group if hasattr(msg, 'is_group') else isinstance(msg.peer_id, tl_types.PeerChat))
    is_channel = getattr(message_or_event, 'is_channel', False) or (msg.is_channel if hasattr(msg, 'is_channel') else isinstance(msg.peer_id, tl_types.PeerChannel))
    
    peer_type = "private" if is_private else ("group" if is_group else "channel")

    # 4. Resolve sender and chat IDs/usernames
    sender_id = str(msg.sender_id) if msg.sender_id else ""
    chat_id = str(msg.chat_id) if msg.chat_id else ""
    
    sender_username = ""
    chat_username = ""
    if hasattr(msg, "sender") and msg.sender and getattr(msg.sender, "username", None):
        sender_username = f"@{msg.sender.username.lower()}"
    if hasattr(message_or_event, "chat") and message_or_event.chat and getattr(message_or_event.chat, "username", None):
        chat_username = f"@{message_or_event.chat.username.lower()}"

    # 5. Standardize trigger details
    trigger_state = "trigger:none"
    if is_trigger_fired:
        trigger_state = "trigger:all"
        t_lower = text_content.lower()
        
        first_name_val = (me.first_name or "").lower()
        last_name_val = (me.last_name or "").lower()
        full_name_val = f"{first_name_val} {last_name_val}".strip()
        
        if first_name_val and first_name_val in t_lower:
            trigger_state = "trigger:first_name"
        elif last_name_val and last_name_val in t_lower:
            trigger_state = "trigger:last_name"
        elif full_name_val and full_name_val in t_lower:
            trigger_state = "trigger:full_name"
        elif me.username and f"@{me.username.lower()}" in t_lower:
            trigger_state = "trigger:username"
        elif getattr(message_or_event, "mentioned", False):
            trigger_state = "trigger:mentioned"
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
            p_val = r.split(":", 1)[1]
            return peer_type == p_val or (p_val == "group" and is_group) or (p_val == "channel" and is_channel) or (p_val == "private" and is_private)
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
            if t_val in ["name", "first_name"] and trigger_state in ["trigger:name", "trigger:first_name"]:
                return True
            return trigger_state == f"trigger:{t_val}"
        if r.startswith("text:"):
            t_val = r.split(":", 1)[1]
            if is_caption: return False
            try: return bool(re.search(t_val, text_content, re.IGNORECASE))
            except Exception: return t_val in text_content.lower()
        if r.startswith("caption:"):
            c_val = r.split(":", 1)[1]
            if not is_caption: return False
            try: return bool(re.search(c_val, text_content, re.IGNORECASE))
            except Exception: return c_val in text_content.lower()
        if r.replace("-", "").isdigit():
            return r in [sender_id, chat_id]
        if r.startswith("@"):
            return r in [sender_username, chat_username]
        try: return bool(re.search(rule_item, text_content, re.IGNORECASE))
        except Exception: return r in text_content.lower()

    w_list = [str(w).strip() for w in whitelist if str(w).strip()] if whitelist else []
    b_list = [str(b).strip() for b in blacklist if str(b).strip()] if blacklist else []

    if b_list:
        for b_rule in b_list:
            if matches_rule(b_rule):
                return False

    if w_list:
        for w_rule in w_list:
            if matches_rule(w_rule):
                return True
        return False
    return default_allow

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

    is_triggered = False
    text_lower = text_content.lower()
    first_name_val = (me.first_name or "").lower()
    last_name_val = (me.last_name or "").lower()
    full_name_val = f"{first_name_val} {last_name_val}".strip()
    
    is_private = event.is_private
    if is_private:
        is_triggered = True
    else:
        if first_name_val and first_name_val in text_lower:
            is_triggered = True
        elif last_name_val and last_name_val in text_lower:
            is_triggered = True
        elif full_name_val and full_name_val in text_lower:
            is_triggered = True
        elif me.username and f"@{me.username.lower()}" in text_lower:
            is_triggered = True
        elif getattr(event, "mentioned", False):
            is_triggered = True
        elif event.message.is_reply:
            is_triggered = True

    whitelist = config.MSG_SAVE_WHITELIST if action_type == "save" else config.MSG_GEN_WHITELIST
    blacklist = config.MSG_SAVE_BLACKLIST if action_type == "save" else config.MSG_GEN_BLACKLIST

    if not matches_advanced_filter(event, me, whitelist, blacklist, is_trigger_fired=is_triggered, default_allow=True):
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
            has_name_trigger = False
            if "name" in config.AI_RESPONSE_TRIGGERS or "first_name" in config.AI_RESPONSE_TRIGGERS:
                if first_name_val and first_name_val in text_lower:
                    has_name_trigger = True
            if "last_name" in config.AI_RESPONSE_TRIGGERS:
                if last_name_val and last_name_val in text_lower:
                    has_name_trigger = True
            if "full_name" in config.AI_RESPONSE_TRIGGERS:
                if full_name_val and full_name_val in text_lower:
                    has_name_trigger = True
            if has_name_trigger:
                triggered = True
            if "username" in config.AI_RESPONSE_TRIGGERS and me.username:
                if f"@{me.username.lower()}" in text_lower:
                    triggered = True
            if "mentioned" in config.AI_RESPONSE_TRIGGERS and event.mentioned:
                triggered = True
            if "reply_to_me" in config.AI_RESPONSE_TRIGGERS and event.message.is_reply:
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'\[{}\]\s*\n(.*?)(?=\n\[|\Z)'.format(re.escape(section_name)), content, re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return default_text

def get_all_project_modules() -> dict:
    """Dynamically traverses the project root directory and registers all available Python modules in execution sandboxes."""
    import sys
    import importlib
    from pathlib import Path
    
    modules = {}
    base_dir = Path(__file__).resolve().parent.parent
    
    # Scan directories in project root, excluding standard caches, git files, and virtual environments
    subdirs = [p for p in base_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in ["bot_workspace", "emoji_cache", "avatar_cache", "gift_cache", "temp_media", "venv"]]
    
    for sub_path in subdirs:
        for file_path in sub_path.glob("**/*.py"):
            if file_path.name.startswith("_") or "pycache" in str(file_path):
                continue
            module_name = file_path.parent.name if file_path.name == "__init__.py" else file_path.stem
            try:
                if module_name in sys.modules:
                    modules[module_name] = sys.modules[module_name]
                else:
                    mod = importlib.import_module(module_name)
                    if mod:
                        modules[module_name] = mod
            except Exception:
                pass
    return modules

async def should_send_read_acknowledge(message_or_event, me, db=None, is_trigger_fired: bool = False) -> bool:
    import config
    return matches_advanced_filter(message_or_event, me, config.READ_ACK_WHITELIST, config.READ_ACK_BLACKLIST, is_trigger_fired=is_trigger_fired, default_allow=True)
