# core/prompt_interpolator.py
import logging
from pathlib import Path
from telethon.tl.functions.users import GetFullUserRequest
from config import BASE_DIR, WORKSPACE_DIR, SESSION_NAME, SESSION_PATH, SAFE_DB_DIR, OWNER_ID, DB_NAME, BOT_AVATAR_NAME

logger = logging.getLogger("PromptInterpolator")

async def get_interpolated_prompt(client, character_file_name, use_system_prompt=True) -> str:
    """
    Dynamically loads prompt templates from config/ directory, fetches active
    Premium Telegram user profiles metadata, and compiles variables safely.
    """
    if not use_system_prompt:
        return "You are an AI assistant."

    # 1. Extracting AI profile data
    try:
        me = await client.get_me()
        me_id = me.id
        me_first = me.first_name or "No name"
        me_last = me.last_name or ""
        me_user = me.username or "no"
        me_phone = me.phone or "hidden"
        me_premium = "yes" if getattr(me, 'premium', False) else "no"
        me_verified = "yes" if getattr(me, 'verified', False) else "no"
        me_scam = "yes" if getattr(me, 'scam', False) else "no"
        me_fake = "yes" if getattr(me, 'fake', False) else "no"
        me_bot = "yes" if getattr(me, 'bot', False) else "no"
        me_restricted = "yes" if getattr(me, 'restricted', False) else "no"
        
        full_me = await client(GetFullUserRequest(me))
        me_bio = getattr(full_me.full_user, 'about', None) or "description missing"
        
        bday_obj = getattr(full_me.full_user, 'birthday', None)
        me_birthday = "hidden"
        if bday_obj:
            me_birthday = f"{bday_obj.day:02d}.{bday_obj.month:02d}"
            if getattr(bday_obj, 'year', None):
                me_birthday += f".{bday_obj.year}"
    except Exception as e:
        logger.error(f"Error getting AI profile for prompt: {str(e)}")
        me_id, me_first, me_last, me_user, me_phone, me_premium, me_bio, me_verified, me_scam, me_fake, me_bot, me_restricted, me_birthday = (
            "hidden", "Baziliksina", "", "baziliksina", "unknown", "no", "AI Assistant", "no", "no", "no", "no", "no", "unknown"
        )

    # 2. Extracting creator's profile data
    try:
        creator = await client.get_entity(OWNER_ID)
        creator_id = creator.id
        creator_first = creator.first_name or "Bazilevs"
        creator_last = creator.last_name or ""
        creator_user = creator.username or "no"
        creator_premium = "yes" if getattr(creator, 'premium', False) else "no"
        creator_verified = "yes" if getattr(creator, 'verified', False) else "no"
        creator_scam = "yes" if getattr(creator, 'scam', False) else "no"
        creator_fake = "yes" if getattr(creator, 'fake', False) else "no"
        creator_bot = "yes" if getattr(creator, 'bot', False) else "no"
        creator_phone = getattr(creator, 'phone', 'hidden') or "hidden"
        creator_restricted = "yes" if getattr(creator, 'restricted', False) else "no"

        full_creator = await client(GetFullUserRequest(creator))
        creator_bio = getattr(full_creator.full_user, 'about', None) or "description missing"
        
        cbday_obj = getattr(full_creator.full_user, 'birthday', None)
        creator_birthday = "hidden"
        if cbday_obj:
            creator_birthday = f"{cbday_obj.day:02d}.{cbday_obj.month:02d}"
            if getattr(cbday_obj, 'year', None):
                creator_birthday += f".{cbday_obj.year}"
    except Exception as e:
        logger.error(f"Error getting creator profile for prompt: {str(e)}")
        creator_id, creator_first, creator_last, creator_user, creator_premium, creator_bio, creator_verified, creator_scam, creator_fake, creator_bot, creator_birthday = (
            OWNER_ID, "Bazilevs", "", "mcpeorakul", "no", "Bot creator", "no", "no", "no", "no", "unknown"
        )

    # 3. Read prompt templates
    core_prompt_template = "You are an AI assistant."
    rules_prompt_template = ""
    character_prompt_template = "You are Baziliksina."

    prompt_dir = BASE_DIR / "config"

    # Resolve dynamic host IP instead of 0.0.0.0 for prompt instructions
    display_host = getattr(config, "WEB_SERVER_HOST", "127.0.0.1")
    if not display_host or display_host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((getattr(config, "WEB_SERVER_IP_DETECTION_HOST", "8.8.8.8"), getattr(config, "WEB_SERVER_IP_DETECTION_PORT", 80)))
            display_host = s.getsockname()[0]
            s.close()
        except Exception:
            display_host = "127.0.0.1"

    # system_prompt.txt
    sys_path = prompt_dir / "system_prompt.txt"
    if sys_path.exists():
        try:
            with open(sys_path, "r", encoding="utf-8") as f:
                core_prompt_template = f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read system_prompt.txt: {str(e)}")

    # rules_prompt.txt
    rules_path = prompt_dir / "rules_prompt.txt"
    if rules_path.exists():
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules_prompt_template = f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read rules_prompt.txt: {str(e)}")

    # character.txt (supports config/character.txt first, falls back to root)
    char_path = prompt_dir / character_file_name
    if not char_path.exists():
        char_path = BASE_DIR / character_file_name
        
    if char_path.exists():
        try:
            with open(char_path, "r", encoding="utf-8", errors="ignore") as f:
                character_prompt_template = f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read character file: {str(e)}")

    # 4. Safe interpolation dictionary
    replacements = {
        "{creator_first}": creator_first,
        "{creator_last}": creator_last,
        "{creator_user}": creator_user,
        "{creator_id}": str(creator_id),
        "{creator_premium}": creator_premium,
        "{creator_verified}": creator_verified,
        "{creator_scam}": creator_scam,
        "{creator_fake}": creator_fake,
        "{creator_bot}": creator_bot,
        "{creator_phone}": creator_phone,
        "{creator_restricted}": creator_restricted,
        "{creator_birthday}": creator_birthday,
        "{creator_bio}": creator_bio,
        "{me_first}": me_first,
        "{me_last}": me_last,
        "{me_user}": me_user,
        "{me_id}": str(me_id),
        "{me_phone}": me_phone,
        "{me_premium}": me_premium,
        "{me_verified}": me_verified,
        "{me_scam}": me_scam,
        "{me_fake}": me_fake,
        "{me_bot}": me_bot,
        "{me_restricted}": me_restricted,
        "{me_birthday}": me_birthday,
        "{me_bio}": me_bio,
        "{WORKSPACE_DIR}": str(WORKSPACE_DIR),
        "{SESSION_NAME}": str(SESSION_NAME),
        "{SESSION_PATH}": str(SESSION_PATH),
        "{SAFE_DB_DIR}": str(SAFE_DB_DIR),
        "{DB_NAME}": str(DB_NAME),
        "{BOT_AVATAR_NAME}": str(BOT_AVATAR_NAME),
        "{WEB_SERVER_HOST}": display_host,
        "{WEB_SERVER_PORT}": str(config.WEB_SERVER_PORT),
        "{WEB_SERVER_SUBDOMAIN}": str(config.WEB_SERVER_SUBDOMAIN)
    }

    core_prompt = core_prompt_template
    for k, v in replacements.items():
        core_prompt = core_prompt.replace(k, v)

    rules_prompt = rules_prompt_template
    for k, v in replacements.items():
        rules_prompt = rules_prompt.replace(k, v)

    char_prompt = character_prompt_template
    for k, v in replacements.items():
        char_prompt = char_prompt.replace(k, v)

    full_technical = f"{char_prompt}\n\n{core_prompt}"
    if rules_prompt:
        full_technical = f"{full_technical}\n\n--- SECTION 9: ACTIVE RULES OF BEHAVIOR ---\n{rules_prompt}"
    return full_technical
