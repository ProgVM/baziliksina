# parser.py
import logging
from telethon.tl import types as tl_types
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest

from downloader import get_cached_premium_emoji, get_cached_avatar, get_cached_gift_animation
from utils import safe_serialize, safe_deserialize

logger = logging.getLogger("Parser")


def get_media_type_description(message) -> str:
    """
    Analyzes the message media and returns a clean, plain English string 
    representing the media type, matching native Telegram reply-header style.
    Supports collaborative checklists / to-do lists.
    """
    if not message.media:
        return None
        
    media_name = type(message.media).__name__
    
    if media_name == "MessageMediaPhoto":
        # Check if the photo belongs to a grouped media album
        if getattr(message, "grouped_id", None) is not None:
            return "Album"
        return "Photo"
        
    elif "ToDo" in media_name or "Todo" in media_name:
        return "List"
        
    elif media_name == "MessageMediaPoll":
        return "Poll"
        
    elif media_name == "MessageMediaGift":
        return "Gift"
        
    elif media_name == "MessageMediaContact":
        return "Contact"
        
    elif media_name in ["MessageMediaGeo", "MessageMediaGeoLive"]:
        return "Location"
        
    elif media_name == "MessageMediaVenue":
        return "Venue"
        
    elif media_name == "MessageMediaDocument":
        doc = message.media.document
        
        is_sticker = False
        is_voice = False
        is_video_note = False
        is_gif = False
        is_video = False
        is_audio = False
        
        # Scan attributes to distinguish various document subtypes
        for attr in getattr(doc, 'attributes', []):
            attr_name = type(attr).__name__
            if attr_name == "DocumentAttributeSticker":
                is_sticker = True
            elif attr_name == "DocumentAttributeAudio":
                if getattr(attr, 'voice', False):
                    is_voice = True
                else:
                    is_audio = True
            elif attr_name == "DocumentAttributeVideo":
                if getattr(attr, 'round_message', False):
                    is_video_note = True
                elif getattr(attr, 'nosound', False):
                    is_gif = True
                else:
                    is_video = True
            elif attr_name == "DocumentAttributeAnimated":
                is_gif = True
                
        if is_sticker:
            return "Sticker"
        elif is_voice:
            return "Voice Message"
        elif is_video_note:
            return "Video Note"
        elif is_gif:
            return "GIF"
        elif is_video:
            return "Video"
        elif is_audio:
            return "Audio"
        else:
            return "File"
            
    return "Media"


async def parse_and_cache_user_metadata(client, db, user) -> dict:
    """
    Asynchronously requests full user information from Telegram, downloads the avatar
    (including .mp4 video avatars) and saves it in the DB with all Premium attributes and business data.
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
    Asynchronously requests full user information from Telegram, downloads the avatar
    (including .mp4 video avatars) and saves it in the DB with all Premium attributes and business data.
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
    """Extracts basic string metadata about the sender for the AI system prompt."""
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
        return f"Regular Group '{title}' [ID: {sender.id}]{badges_str}"
        
    return f"Entity {p_type} [ID: {getattr(sender, 'id', 'hidden')}]{badges_str}"


async def parse_message_payload(client, db, message) -> str:
    """
    Recursively analyzes the message, extracts and caches premium emojis,
    Star Gift animations, and structural attachment parameters, outputting complete raw metadata
    directly into the history so the multimodal AI can track and reuse specific IDs.
    """
    meta_parts = []
    text = message.text or ""
    chat_id = str(message.chat_id)
    msg_id = message.id

    raw_meta_dict = {
        "to_dict_raw": message.to_dict() if hasattr(message, "to_dict") else {}
    }

    # 1. Parsing and caching Premium custom emojis in-place
    if message.entities:
        emoji_refs = []
        for ent in message.entities:
            if isinstance(ent, tl_types.MessageEntityCustomEmoji):
                doc_id = ent.document_id
                local_path = await get_cached_premium_emoji(client, doc_id, is_animated=False)
                ref_str = f"[Custom Premium Emoji ID: {doc_id} (Local path: {local_path or 'not downloaded'})]"
                emoji_refs.append(ref_str)
        if emoji_refs:
            meta_parts.append("\n".join(emoji_refs))

    # 2. Parsing Star Gifts with animations
    if message.media and type(message.media).__name__ == "MessageMediaGift":
        gift = message.media
        gift_text = getattr(gift, "text", "") or ""
        sender_gift_id = getattr(gift, "from_id", "anonymously")
        gift_id = getattr(gift, "gift_id", None)
        local_gift_path = await get_cached_gift_animation(client, gift_id) if gift_id else None
        gift_ref = f"[Star Gift Received | ID: {gift_id or 'unknown'} | Sender: {sender_gift_id} | Text: '{gift_text}' | Animation path: '{local_gift_path or 'not downloaded'}']"
        meta_parts.append(gift_ref)

    # 3. Extract complete raw MTProto parameters for attached media
    media_desc = get_media_type_description(message)
    if media_desc:
        media_id = "unknown"
        access_hash = "unknown"
        file_ref_hex = "none"
        if hasattr(message.media, "document") and message.media.document:
            doc = message.media.document
            media_id = doc.id
            access_hash = doc.access_hash
            file_ref_hex = doc.file_reference.hex() if doc.file_reference else "none"
        elif hasattr(message.media, "photo") and message.media.photo:
            photo = message.media.photo
            media_id = photo.id
            access_hash = photo.access_hash
            file_ref_hex = photo.file_reference.hex() if photo.file_reference else "none"
        meta_parts.append(f"[Attached Media - Type: {media_desc} | ID: {media_id} | Access Hash: {access_hash} | File Reference (Hex): {file_ref_hex}]")

    # Save all visual/secondary message metadata in msgs_meta
    meta_text_block = "\n".join(meta_parts).strip()
    if meta_text_block:
        await db.save_msg_meta(chat_id, msg_id, meta_text=meta_text_block, raw_meta_dict=raw_meta_dict)

    # If the message has no text but has media, return the media descriptor as fallback
    if not text and media_desc:
        return f"[{media_desc}]"

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
                original_text = orig_text
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
