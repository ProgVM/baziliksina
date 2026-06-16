# parser.py
import logging
from telethon.tl import types as tl_types
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest

from downloader import get_cached_premium_emoji, get_cached_avatar, get_cached_gift_animation
from utils import safe_serialize, safe_deserialize

logger = logging.getLogger("Parser")


async def parse_and_cache_user_metadata(client, db, user) -> dict:
    """
    Асинхронно запрашивает у Telegram полную информацию о пользователе, скачивает аватарку
    (включая видео-аватарку .mp4) и сохраняет in БД со всеми Premium-атрибутами и бизнес-данными.
    """
    if not user:
        return {}

    user_id = str(user.id)
    logger.info(f"Collecting and caching full metadata of user ID {user_id}...")

    username = getattr(user, "username", None)
    first_name = getattr(user, "first_name", "") or ""
    last_name = getattr(user, "last_name", "") or ""
    phone = getattr(user, "phone", None)
    premium = 1 if getattr(user, "premium", False) else 0
    verified = 1 if getattr(user, "verified", False) else 0
    scam = 1 if getattr(user, "scam", False) else 0
    fake = 1 if getattr(user, "fake", False) else 0

    bio = None
    birthday = None
    emoji_status_id = None
    avatar_path = None
    personal_channel = None
    business_address = None
    business_location = None
    business_working_hours = None

    if getattr(user, "emoji_status", None):
        e_status = user.emoji_status
        if isinstance(e_status, tl_types.EmojiStatus):
            emoji_status_id = str(e_status.document_id)
            await get_cached_premium_emoji(client, e_status.document_id, is_animated=True)

    try:
        full_req = await client(GetFullUserRequest(user))
        full_user = full_req.full_user

        bio = getattr(full_user, "about", None)
        
        bday_obj = getattr(full_user, "birthday", None)
        if bday_obj:
            birthday = f"{bday_obj.day:02d}.{bday_obj.month:02d}"
            if getattr(bday_obj, "year", None):
                birthday += f".{bday_obj.year}"

        p_channel = getattr(full_user, "personal_channel", None)
        if p_channel:
            personal_channel = f"https://t.me/c/{p_channel.channel_id}"

        biz_work = getattr(full_user, "business_work_hours", None)
        if biz_work:
            business_working_hours = str(biz_work)
        biz_address = getattr(full_user, "business_intro", None)
        if biz_address:
            business_address = getattr(biz_address, "description", None)

    except Exception as e:
        logger.debug(f"Failed to get full GetFullUserRequest data for {user_id}: {str(e)}")

    try:
        has_video = getattr(user, "photo", None) and getattr(user.photo, "has_video", False)
        avatar_path = await get_cached_avatar(client, user, is_video=has_video)
    except Exception as e:
        logger.debug(f"Error downloading avatar for {user_id}: {str(e)}")

    raw_meta = {
        "raw_user_api": user.to_dict() if hasattr(user, "to_dict") else {},
        "premium_color_index": getattr(user, "color", None).color if getattr(user, "color", None) else None,
        "background_emoji_id": getattr(user, "color", None).background_emoji_id if getattr(user, "color", None) else None,
        "profile_color_index": getattr(user, "profile_color", None).color if getattr(user, "profile_color", None) else None,
        "personal_channel_link": personal_channel,
        "business_address": business_address
    }

    meta_dict = {
        "id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "bio": bio,
        "premium": premium,
        "verified": verified,
        "scam": scam,
        "fake": fake,
        "birthday": birthday,
        "emoji_status_id": emoji_status_id,
        "avatar_path": avatar_path,
        "raw_meta_json": raw_meta
    }

    await db.save_user_meta(user_id, meta_dict)
    return meta_dict


async def parse_and_cache_chat_metadata(client, db, chat) -> dict:
    """
    Асинхронно запрашивает полную информацию о группе, супергруппе или канале,
    скачивает их логотипы и сохраняет in БД chats_meta с сырыми структурами.
    """
    if not chat:
        return {}

    chat_id = str(chat.id)
    if not chat_id.startswith("-") and type(chat).__name__ in ["Channel", "Chat"]:
        chat_id = f"-100{chat_id}" if type(chat).__name__ == "Channel" else f"-{chat_id}"

    logger.info(f"Collecting and caching metadata of chat/channel ID {chat_id}...")

    title = getattr(chat, "title", "Group")
    username = getattr(chat, "username", None)
    chat_type = type(chat).__name__

    bio = None
    description = None
    photo_path = None
    linked_chat_id = None

    try:
        if chat_type == "Channel":
            full_req = await client(GetFullChannelRequest(chat))
        elif chat_type == "Chat":
            full_req = await client(GetFullChatRequest(chat.id))
        else:
            full_req = None

        if full_req:
            full_chat = full_req.full_chat
            bio = getattr(full_chat, "about", None)
            description = getattr(full_chat, "about", None)
            
            linked = getattr(full_chat, "linked_chat_id", None)
            if linked:
                linked_chat_id = str(linked)
    except Exception as e:
        logger.debug(f"Failed to get full chat/channel data {chat_id}: {str(e)}")

    try:
        photo_path = await get_cached_avatar(client, chat, is_video=False)
    except Exception as e:
        logger.debug(f"Error downloading chat photo {chat_id}: {str(e)}")

    raw_meta = {
        "raw_chat_api": chat.to_dict() if hasattr(chat, "to_dict") else {}
    }

    meta_dict = {
        "id": chat_id,
        "title": title,
        "username": username,
        "type": chat_type,
        "bio": bio,
        "description": description,
        "photo_path": photo_path,
        "linked_chat_id": linked_chat_id,
        "raw_meta_json": raw_meta
    }

    await db.save_chat_meta(chat_id, meta_dict)
    return meta_dict


def parse_sender_info(sender, message) -> str:
    """Извлекает базовые строковые метаданные об отправителе для системного промпта AI."""
    if not sender:
        return "Unknown sender"
    
    p_type = type(sender).__name__
    badges = []
    
    if getattr(sender, 'premium', False):
        badges.append("Premium")
    if getattr(sender, 'verified', False):
        badges.append("Verified")
    if getattr(sender, 'scam', False):
        badges.append("SCAM")
    if getattr(sender, 'fake', False):
        badges.append("FAKE")
        
    badges_str = f" [{' | '.join(badges)}]" if badges else ""
    username = getattr(sender, 'username', None)
    user_ref = f" (@{username})" if username else ""
    
    if p_type == "User":
        entity_kind = "Bot" if getattr(sender, 'bot', False) else "User"
        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        name = f"{first_name} {last_name}".strip() or "User"
        return f"{entity_kind} '{name}'{user_ref} [ID: {sender.id}]{badges_str}"
        
    elif p_type == "Channel":
        is_group = getattr(sender, 'megagroup', False) or getattr(sender, 'gigagroup', False)
        entity_kind = "Supergroup" if is_group else "Channel"
        title = getattr(sender, 'title', 'Channel')
        post_author = getattr(message, 'post_author', None)
        author_sig = f" (author signature: '{post_author}')" if post_author else ""
        return f"{entity_kind} '{title}'{user_ref} [ID: {sender.id}]{badges_str}{author_sig}"
        
    elif p_type == "Chat":
        title = getattr(sender, 'title', 'Group')
        return f"Обычная Group '{title}' [ID: {sender.id}]{badges_str}"
        
    return f"Entity {p_type} [ID: {getattr(sender, 'id', 'hidden')}]{badges_str}"


async def parse_message_payload(client, db, message) -> str:
    """
    Рекурсивно анализирует сообщение, извлекает и кэширует премиум-эмодзи,
    анимации Star Gifts, структуру инлайн-кнопок, а также сохраняет свойства реакций 
    и сырые свойства сообщения in отдельную таблицу msgs_meta in формате JSON.
    """
    meta_parts = []
    text = message.message or ""
    chat_id = str(message.chat_id)
    msg_id = message.id

    raw_meta_dict = {
        "to_dict_raw": message.to_dict() if hasattr(message, "to_dict") else {}
    }

    # 1. Collection and automatic caching of premium emojis from text
    if message.entities:
        emoji_refs = []
        for ent in message.entities:
            if isinstance(ent, tl_types.MessageEntityCustomEmoji):
                doc_id = ent.document_id
                local_path = await get_cached_premium_emoji(client, doc_id, is_animated=False)
                if local_path:
                    emoji_refs.append(f"[Custom emoji ID: {doc_id} (Local path: {local_path})]")
                else:
                    emoji_refs.append(f"[Custom emoji ID: {doc_id}]")
        if emoji_refs:
            meta_parts.append("\n".join(emoji_refs))

    # 2. Analysis of Star Gifts and automatic downloading of .tgs animations
    if message.media and type(message.media).__name__ == "MessageMediaGift":
        gift = message.media
        gift_text = getattr(gift, "text", "") or ""
        sender_gift_id = getattr(gift, "from_id", "anonymously")
        
        gift_id = getattr(gift, "gift_id", None)
        local_gift_path = None
        if gift_id:
            local_gift_path = await get_cached_gift_animation(client, gift_id)

        gift_ref = (
            f"[Системное событие: Получен подарок Telegram Star Gift]\n"
            f"- Отправитель ID: {sender_gift_id}\n"
            f"- Gift text: '{gift_text}'\n"
            f"- Анимация подарка локально: '{local_gift_path or 'not downloaded'}'"
        )
        meta_parts.append(gift_ref)

    # 3. Parsing of system actions (Giveaways, Premium, Pings, etc.)
    if message.action:
        act = message.action
        act_name = type(act).__name__
        meta_parts.append(f"[Service event ({act_name})]")

    # 4. DEEP ANALYSIS OF ATTACHMENTS (Specific stickers, video notes, GIFs and audio)
    if message.media:
        media_name = type(message.media).__name__
        
        if media_name == "MessageMediaPhoto":
            meta_parts.append("[Image attached]")
            
        elif media_name == "MessageMediaDocument":
            doc = message.media.document
            is_sticker = False
            is_voice = False
            is_video_note = False
            is_gif = False
            is_video = False
            is_audio = False
            sticker_emoji = "hidden"
            file_name = "unnamed"
            
            for attr in getattr(doc, 'attributes', []):
                if isinstance(attr, tl_types.DocumentAttributeSticker):
                    is_sticker = True
                    sticker_emoji = attr.alt or "hidden"
                elif isinstance(attr, tl_types.DocumentAttributeAudio):
                    if getattr(attr, 'voice', False):
                        is_voice = True
                    else:
                        is_audio = True
                elif isinstance(attr, tl_types.DocumentAttributeVideo):
                    if getattr(attr, 'round_message', False):
                        is_video_note = True
                    elif getattr(attr, 'nosound', False):
                        is_gif = True
                    else:
                        is_video = True
                elif isinstance(attr, tl_types.DocumentAttributeAnimated):
                    is_gif = True
                elif isinstance(attr, tl_types.DocumentAttributeFilename):
                    file_name = attr.file_name

            if is_sticker:
                meta_parts.append(f"[Sticker attached: {sticker_emoji}]")
            elif is_voice:
                meta_parts.append("[Voice message attached]")
            elif is_video_note:
                meta_parts.append("[Round video note / video circle attached]")
            elif is_gif:
                meta_parts.append("[GIF animation attached]")
            elif is_video:
                meta_parts.append("[Video clip attached]")
            elif is_audio:
                meta_parts.append("[Audio file attached]")
            else:
                meta_parts.append(f"[File attached: '{file_name}']")

    # 5. Primary extraction and saving of reactions on incoming/outgoing message
    if getattr(message, 'reactions', None) and getattr(message.reactions, 'results', None):
        rx_parts = []
        for rc in message.reactions.results:
            if hasattr(rc.reaction, 'emoticon'):
                rx_parts.append(f"'{rc.reaction.emoticon}' (x{rc.count})")
            elif hasattr(rc.reaction, 'document_id'):
                rx_parts.append(f"[Custom emoji ID {rc.reaction.document_id}] (x{rc.count})")
        if rx_parts:
            meta_parts.append("[Reactions on message]: " + " | ".join(rx_parts))

    # Сохраняем все визуальные/второстепенные метаданные сообщения in msgs_meta
    meta_text_block = "\n".join(meta_parts).strip()
    if meta_text_block:
        await db.save_msg_meta(chat_id, msg_id, meta_text=meta_text_block, raw_meta_dict=raw_meta_dict)

    return text


async def parse_reply_metadata(message, current_chat_id: str, client_instance, db_instance) -> str:
    """
    Resolves cross-chat replies and selected quote fragments with full integration into secondary metadata.
    """
    if not message.reply_to:
        return ""

    header = message.reply_to
    reply_to_id = header.reply_to_msg_id
    quote_text = getattr(header, "quote_text", None)
    peer = getattr(header, "reply_to_peer_id", None)
    
    target_chat_id = str(current_chat_id)
    is_cross_chat = False
    chat_name_ref = "another chat"

    if peer:
        is_cross_chat = True
        peer_name = type(peer).__name__
        if peer_name == "PeerUser":
            target_chat_id = str(peer.user_id)
        elif peer_name == "PeerChat":
            target_chat_id = str(peer.chat_id)
        elif peer_name == "PeerChannel":
            target_chat_id = str(peer.channel_id)
            if not target_chat_id.startswith("-"):
                target_chat_id = f"-100{target_chat_id}"

    original_text = None
    orig_sender = "User"
    
    try:
        async with db_instance.db.execute(
            "SELECT role, text FROM messages WHERE chat_id = ? AND msg_id = ? LIMIT 1",
            (target_chat_id, reply_to_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                orig_role, orig_text = row
                orig_sender = "AI" if orig_role == "model" else "User"
                original_text = f"'{orig_text[:120]}...'" if orig_text else "[Media attachment]"
    except Exception:
        pass

    meta_lines = []
    if is_cross_chat:
        meta_lines.append(f"[Reply to message #{reply_to_id} in {chat_name_ref}]")
    else:
        meta_lines.append(f"[Reply to message #{reply_to_id}]")

    if original_text:
        meta_lines.append(f"[Original text ({orig_sender}): {original_text}]")

    if quote_text:
        meta_lines.append(f"[Selected fragment / Quote]: '{quote_text}'")

    return "\n".join(meta_lines) + "\n"
