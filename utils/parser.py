# utils/parser.py
import logging
import config
from telethon.tl import types as tl_types
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from downloader import get_cached_premium_emoji, get_cached_avatar, get_cached_gift_animation
from utils import safe_serialize, safe_deserialize

logger = logging.getLogger("Parser")

def parse_reply_markup(markup) -> str:
    if not markup:
        return ""
    
    markup_name = type(markup).__name__
    buttons_text = []
    
    if hasattr(markup, 'rows'):
        for row in markup.rows:
            row_btns = []
            for btn in row.buttons:
                btn_type = type(btn).__name__
                btn_info = f"'{btn.text}'"
                if btn_type == "KeyboardButtonCallback":
                    if hasattr(btn, 'data') and btn.data:
                        try:
                            btn_info += f" (callback_data: '{btn.data.decode('utf-8')}')"
                        except Exception:
                            btn_info += f" (callback_hex: '{btn.data.hex()}')"
                elif btn_type == "KeyboardButtonUrl":
                    if hasattr(btn, 'url') and btn.url:
                        btn_info += f" (url: '{btn.url}')"
                elif btn_type == "KeyboardButtonRequestPhone":
                    btn_info += " (requests phone)"
                elif btn_type == "KeyboardButtonRequestGeo":
                    btn_info += " (requests location)"
                elif btn_type == "KeyboardButtonRequestPoll":
                    btn_info += " (requests poll)"
                elif btn_type == "KeyboardButtonSwitchInline":
                    btn_info += f" (switch_inline: '{getattr(btn, 'query', '')}')"
                row_btns.append(btn_info)
            if row_btns:
                buttons_text.append(" | ".join(row_btns))
                
    if not buttons_text:
        return ""
        
    kind = "Inline buttons" if "inline" in markup_name.lower() or "callback" in str(buttons_text).lower() or "url" in str(buttons_text).lower() else "Reply Keyboard buttons"
    return f"[{kind} in this message]:\n" + "\n".join(buttons_text)

def get_phone_region(phone: str) -> str:
    if not phone:
        return "Unknown"
    p = phone.strip("+")
    
    if p.startswith("888"):
        return "Fragment Anonymous Number (+888)"
        
    prefixes = {
        "1": "United States / Canada",
        "7": "Russia / Kazakhstan",
        "20": "Egypt",
        "27": "South Africa",
        "30": "Greece",
        "31": "Netherlands",
        "32": "Belgium",
        "33": "France",
        "34": "Spain",
        "36": "Hungary",
        "39": "Italy",
        "40": "Romania",
        "41": "Switzerland",
        "43": "Austria",
        "44": "United Kingdom",
        "45": "Denmark",
        "46": "Sweden",
        "47": "Norway",
        "48": "Poland",
        "49": "Germany",
        "51": "Peru",
        "52": "Mexico",
        "53": "Cuba",
        "54": "Argentina",
        "55": "Brazil",
        "56": "Chile",
        "57": "Colombia",
        "60": "Malaysia",
        "61": "Australia",
        "62": "Indonesia",
        "63": "Philippines",
        "65": "Singapore",
        "66": "Thailand",
        "81": "Japan",
        "82": "South Korea",
        "84": "Vietnam",
        "86": "China",
        "90": "Turkey",
        "91": "India",
        "92": "Pakistan",
        "93": "Afghanistan",
        "94": "Sri Lanka",
        "95": "Myanmar",
        "98": "Iran",
        "212": "Morocco",
        "213": "Algeria",
        "216": "Tunisia",
        "234": "Nigeria",
        "254": "Kenya",
        "351": "Portugal",
        "352": "Luxembourg",
        "353": "Ireland",
        "354": "Iceland",
        "358": "Finland",
        "370": "Lithuania",
        "371": "Latvia",
        "372": "Estonia",
        "373": "Moldova",
        "374": "Armenia",
        "375": "Belarus",
        "380": "Ukraine",
        "381": "Serbia",
        "385": "Croatia",
        "386": "Slovenia",
        "387": "Bosnia and Herzegovina",
        "420": "Czech Republic",
        "421": "Slovakia",
        "852": "Hong Kong",
        "853": "Macau",
        "886": "Taiwan",
        "961": "Lebanon",
        "962": "Jordan",
        "963": "Syria",
        "964": "Iraq",
        "965": "Kuwait",
        "966": "Saudi Arabia",
        "967": "Yemen",
        "968": "Oman",
        "971": "United Arab Emirates",
        "972": "Israel",
        "973": "Bahrain",
        "974": "Qatar",
        "992": "Tajikistan",
        "993": "Turkmenistan",
        "994": "Azerbaijan",
        "995": "Georgia",
        "996": "Kyrgyzstan",
        "998": "Uzbekistan",
    }
    
    for length in range(4, 0, -1):
        sub = p[:length]
        if sub in prefixes:
            return prefixes[sub]
            
    return "International Prefix"

def get_media_type_description(message) -> str:
    if not message.media:
        return None
        
    media_name = type(message.media).__name__
    
    if media_name == "MessageMediaPhoto":
        if getattr(message, "grouped_id", None) is not None:
            return "Album"
        return "Photo"
        
    elif media_name == "MessageMediaToDo":
        todo = getattr(message.media, "todo", None)
        title_text = todo.title.text if todo and hasattr(todo, "title") and hasattr(todo.title, "text") else "Checklist"
        
        completions_map = {}
        completions_list = getattr(message.media, "completions", None) or []
        for comp in completions_list:
            i_id = getattr(comp, "item_id", None)
            u_id = getattr(comp, "user_id", None)
            if i_id is not None and u_id is not None:
                completions_map.setdefault(i_id, []).append(str(u_id))
                
        items_info = []
        if todo and hasattr(todo, "list") and todo.list:
            for item in todo.list:
                item_title = item.title.text if hasattr(item, "title") and hasattr(item.title, "text") else "Task"
                completed_by_users = completions_map.get(item.id, [])
                if getattr(item, "completed", False) or completed_by_users:
                    users_str = f" by user(s): {', '.join(completed_by_users)}" if completed_by_users else ""
                    mark_str = f"✓ Completed{users_str}"
                else:
                    mark_str = "✗ Pending"
                items_info.append(f"  [{mark_str}] {item_title} (ID: {item.id})")
        items_str = "\n".join(items_info)
        return f"[Native Telegram Checklist: '{title_text}']\n{items_str}"
        
    elif media_name == "MessageMediaPoll":
        poll_obj = getattr(message.media, "poll", None)
        results_obj = getattr(message.media, "results", None)
        
        question = getattr(poll_obj, "question", "") if poll_obj else ""
        is_quiz = getattr(poll_obj, "quiz", False) if poll_obj else False
        is_closed = getattr(poll_obj, "closed", False) if poll_obj else False
        open_answers = getattr(poll_obj, "open_answers", False) if poll_obj else False
        revoting_disabled = getattr(poll_obj, "revoting_disabled", False) if poll_obj else False
        
        total_voters = getattr(results_obj, "total_voters", 0) if results_obj else 0
        voters_by_option = {}
        chosen_options = set()
        correct_options = set()
        
        if results_obj and hasattr(results_obj, "results") and results_obj.results:
            for r_item in results_obj.results:
                opt_key = getattr(r_item, "option", b"").decode("utf-8", errors="ignore")
                v_count = getattr(r_item, "voters", 0)
                voters_by_option[opt_key] = v_count
                if getattr(r_item, "chosen", False):
                    chosen_options.add(opt_key)
                if getattr(r_item, "correct", False):
                    correct_options.add(opt_key)
                    
        solution_text = getattr(results_obj, "solution", "") or getattr(message.media, "solution", "") if results_obj else ""
        
        answers_info = []
        if poll_obj and hasattr(poll_obj, "answers") and poll_obj.answers:
            for idx, ans in enumerate(poll_obj.answers):
                ans_text = getattr(ans, "text", "")
                opt_id = getattr(ans, "option", str(idx).encode("utf-8")).decode("utf-8", errors="ignore")
                v_count = voters_by_option.get(opt_id, 0)
                pct = f"({(v_count/total_voters*100):.1f}%)" if total_voters > 0 else "(0%)"
                
                status_tags = []
                if opt_id in chosen_options:
                    status_tags.append("Chosen by bot")
                if opt_id in correct_options:
                    status_tags.append("Correct answer")
                tag_str = f" [{', '.join(status_tags)}]" if status_tags else ""
                
                answers_info.append(f"  - Option {idx+1}: '{ans_text}' -> Votes: {v_count} {pct}{tag_str}")
                
        answers_str = "\n".join(answers_info)
        solution_str = f"\nQuiz Solution / Explanation: '{solution_text}'" if solution_text else ""
        
        attached_media = getattr(message.media, "attached_media", None)
        has_media_str = f" | Attached Media: {type(attached_media).__name__}" if attached_media else ""
        
        return f"[Telegram Poll/Quiz: '{question}' | Total Voters: {total_voters} | Quiz: {is_quiz} | Closed: {is_closed} | Open Answers: {open_answers} | Revoting Disabled: {revoting_disabled}{has_media_str}]\n{answers_str}{solution_str}"
        
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
    if not user:
        return {}

    user_id = str(user.id)
    logger.info(f"Collecting and caching full metadata of user ID {user_id}...")

    try:
        from config import PROFILE_UPDATE_INTERVAL
        from datetime import datetime, timezone
        cached = await db.get_user_meta(user_id)
        if cached:
            db_ts_str = cached.get("timestamp")
            if db_ts_str:
                db_dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        db_dt = datetime.strptime(db_ts_str, fmt)
                        break
                    except ValueError:
                        continue
                
                if db_dt:
                    delta = (datetime.now(timezone.utc).replace(tzinfo=None) - db_dt).total_seconds()
                    if delta < PROFILE_UPDATE_INTERVAL:
                        logger.debug(f"Profile cache for user {user_id} is fresh ({int(delta)}s old). Skipping API requests.")
                        return cached
    except Exception as cache_err:
        logger.error(f"Error checking user cache freshness in DB: {str(cache_err)}")

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

    phone_region = get_phone_region(phone) if phone else "Unknown"
    restrictions_list = []
    if getattr(user, "restriction_reason", None):
        for r in user.restriction_reason:
            restrictions_list.append({
                "platform": getattr(r, "platform", "all"),
                "reason": getattr(r, "reason", "restricted"),
                "text": getattr(r, "text", "")
            })

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
        "business_address": business_address,
        "phone_region": phone_region,
        "restrictions": restrictions_list
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
    if not chat:
        return {}

    chat_id = str(chat.id)
    if not chat_id.startswith("-") and type(chat).__name__ in ["Channel", "Chat"]:
        chat_id = f"-100{chat_id}" if type(chat).__name__ == "Channel" else f"-{chat_id}"

    logger.info(f"Collecting and caching metadata of chat/channel ID {chat_id}...")

    try:
        from config import PROFILE_UPDATE_INTERVAL
        from datetime import datetime, timezone
        cached = await db.get_chat_meta(chat_id)
        if cached:
            db_ts_str = cached.get("timestamp")
            if db_ts_str:
                db_dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        db_dt = datetime.strptime(db_ts_str, fmt)
                        break
                    except ValueError:
                        continue
                
                if db_dt:
                    delta = (datetime.now(timezone.utc).replace(tzinfo=None) - db_dt).total_seconds()
                    if delta < PROFILE_UPDATE_INTERVAL:
                        logger.debug(f"Profile cache for chat {chat_id} is fresh ({int(delta)}s old). Skipping API requests.")
                        return cached
    except Exception as cache_err:
        logger.error(f"Error checking chat cache freshness in DB: {str(cache_err)}")

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
    phone_val = getattr(sender, 'phone', None)
    phone_ref = f" (Phone: +{phone_val})" if phone_val else ""
    if p_type == "User":
        entity_kind = "Bot" if getattr(sender, 'bot', False) else "User"
        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        name = f"{first_name} {last_name}".strip() or "User"
        return f"{entity_kind} '{name}'{user_ref}{phone_ref if 'phone_ref' in locals() else ''} [ID: {sender.id}]{badges_str}"
        
    elif p_type == "Channel":
        is_group = getattr(sender, 'megagroup', False) or getattr(sender, 'gigagroup', False)
        is_anonymous = not getattr(message, 'post', False) and (getattr(message, 'is_group', False) or (hasattr(message, 'peer_id') and isinstance(message.peer_id, tl_types.PeerChannel)))
        entity_kind = "Supergroup" if is_group else "Channel"
        title = getattr(sender, 'title', 'Channel')
        post_author = getattr(message, 'post_author', None)
        author_sig = f" (author signature: '{post_author}')" if post_author else ""
        anonymous_label = " [ANONYMOUS SENDER - this is a user writing anonymously on behalf of this group/channel]" if is_anonymous else ""
        return f"{entity_kind} '{title}'{user_ref}{phone_ref if 'phone_ref' in locals() else ''}{anonymous_label} [ID: {sender.id}]{badges_str}{author_sig}"
        
    elif p_type == "Chat":
        title = getattr(sender, 'title', 'Group')
        return f"Regular Group '{title}' [ID: {sender.id}]{badges_str}"
        
    return f"Entity {p_type} [ID: {getattr(sender, 'id', 'hidden')}]{badges_str}"

async def parse_message_payload(client, db, message) -> str:
    meta_parts = []
    text = message.text or ""
    
    try:
        me = await client.get_me()
        if message.sender_id != me.id and text:
            text = text.replace("[", r"\[").replace("]", r"\]")
            text = text.replace("<", r"\<").replace(">", r"\>")
    except Exception:
        pass

    chat_id = str(message.chat_id)
    msg_id = message.id

    raw_meta_dict = {
        "to_dict_raw": message.to_dict() if hasattr(message, "to_dict") else {}
    }

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

    if message.entities:
        formatting_refs = []
        for ent in message.entities:
            ent_type = type(ent).__name__
            if ent_type in ["MessageEntityHeader", "MessageEntityTable", "MessageEntityBlockquote", "MessageEntitySubscript", "MessageEntitySuperscript", "MessageEntityMarked", "MessageEntityStrike", "MessageEntityUnderline"]:
                offset = ent.offset
                length = ent.length
                plain_text = message.message or ""
                try:
                    utf16_text = plain_text.encode('utf-16-le')
                    sliced = utf16_text[offset*2:(offset+length)*2].decode('utf-16-le')
                    kind = ent_type.replace('MessageEntity', '')
                    if getattr(ent, "collapsed", False) or getattr(ent, "expandable", False):
                        kind += " (Expandable/Collapsible)"
                    ref_str = f"[{kind}: '{sliced}']"
                    formatting_refs.append(ref_str)
                except Exception:
                    pass
        if formatting_refs:
            meta_parts.append("\n".join(formatting_refs))
            
    if message.reply_markup:
        markup_text = parse_reply_markup(message.reply_markup)
        if markup_text:
            meta_parts.append(markup_text)

    if message.media and type(message.media).__name__ == "MessageMediaGift":
        gift = message.media
        gift_text = getattr(gift, "text", "") or ""
        sender_gift_id = getattr(gift, "from_id", "anonymously")
        gift_id = getattr(gift, "gift_id", None)
        local_gift_path = await get_cached_gift_animation(client, gift_id) if gift_id else None
        gift_ref = f"[Star Gift Received | ID: {gift_id or 'unknown'} | Sender: {sender_gift_id} | Text: '{gift_text}' | Animation path: '{local_gift_path or 'not downloaded'}']"
        meta_parts.append(gift_ref)

    if message.media and type(message.media).__name__ == "MessageMediaWebPage":
        webpage = message.media.webpage
        if type(webpage).__name__ == "WebPage":
            wp_title = getattr(webpage, "title", "") or ""
            wp_site = getattr(webpage, "site_name", "") or ""
            wp_desc = getattr(webpage, "description", "") or ""
            wp_url = getattr(webpage, "url", "") or ""
            meta_parts.append(f"[WebPage Article Preview | Site: '{wp_site}' | Title: '{wp_title}' | URL: '{wp_url}' | Desc: '{wp_desc}']")

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

    meta_text_block = "\n".join(meta_parts).strip()
    if meta_text_block:
        await db.save_msg_meta(chat_id, msg_id, meta_text=meta_text_block, raw_meta_dict=raw_meta_dict)

    if not text and media_desc:
        return f"[{media_desc}]"

    return text

async def parse_reply_metadata(message, current_chat_id: str, client_instance, db_instance) -> str:
    if not message.reply_to:
        return ""

    meta_lines = []
    
    async def traverse_chain(msg_id, chat_id, level=1):
        if level > config.RECURSIVE_REPLY_DEPTH_LIMIT:
            return
        role = None
        text = None
        try:
            async with db_instance.db.execute(
                "SELECT role, text FROM messages WHERE chat_id = ? AND msg_id = ? LIMIT 1",
                (str(chat_id), int(msg_id))
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    role, text = row
        except Exception:
            pass
        if not text:
            try:
                orig_msg = await client_instance.get_messages(chat_id, ids=int(msg_id))
                if orig_msg:
                    text = orig_msg.message or ""
                    role = "model" if orig_msg.sender_id == (await client_instance.get_me()).id else "user"
                    await db_instance.save_message(str(chat_id), role, text, None, orig_msg.id)
            except Exception:
                pass
        if text:
            sender_label = "AI" if role == "model" else "User"
            meta_lines.append("  " * (level - 1) + f"└─ [Parent Message #{msg_id} ({sender_label})]: '{text}'")
            parent_reply_to_id = None
            try:
                orig_msg = await client_instance.get_messages(chat_id, ids=int(msg_id))
                if orig_msg and orig_msg.reply_to:
                    parent_reply_to_id = orig_msg.reply_to.reply_to_msg_id
            except Exception:
                pass
            if parent_reply_to_id:
                await traverse_chain(parent_reply_to_id, chat_id, level + 1)

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

    if is_cross_chat:
        meta_lines.append(f"[Reply to message #{reply_to_id} in {chat_name_ref}]")
    else:
        meta_lines.append(f"[Reply to message #{reply_to_id}]")

    await traverse_chain(reply_to_id, target_chat_id, level=1)

    if quote_text:
        meta_lines.append(f"[Selected quote / Quote fragment]: '{quote_text}'")

    return "\n".join(meta_lines) + "\n"
