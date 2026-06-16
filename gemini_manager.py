# gemini_manager.py
import json
import os
import asyncio
import logging
import hashlib
import inspect
from google.genai import types
from google.genai.errors import APIError

from config import GEMINI_MODELS, WORKSPACE_DIR, SESSION_NAME, SESSION_PATH, SAFE_DB_DIR, OWNER_ID, MESSAGES_LIMIT, SUMMARIZATION_MESSAGES_LIMIT, SUMMARIZATION_KEEP_LIMIT, TEMPERATURE, STOP_SEQUENCES, THINKING_LEVEL, TOP_P, MAX_TURNS, MEDIA_LIMIT, SAFETY_HATE_SPEECH, SAFETY_HARASSMENT, SAFETY_SEXUALLY_EXPLICIT, SAFETY_DANGEROUS_CONTENT, GEMINI_TIMEOUT, TYPING_INTERVAL, TIMEOUT_SLEEP, RATE_LIMIT_SLEEP, API_ERROR_SLEEP, CHARACTER_FILE
from key_manager import GeminiKeyManager, PollinationsKeyManager
from db_manager import DBManager
from registry import registry
import tools

logger = logging.getLogger("GeminiManager")


class GeminiManager:
    def __init__(self, telegram_client, db_manager):
        self.client = telegram_client
        self.db = db_manager
        self.key_manager = GeminiKeyManager(db_manager)
        self.pollinations_key_manager = PollinationsKeyManager(db_manager)
        self._last_system_prompt_hash = None
        
        # Pool of all system tool names for the text race condition bypass regular expression
        import re
        tool_names = [
            "save_file_to_workspace", "save_file_from_telegram", "read_file_from_workspace", "list_workspace_files", "delete_file_from_workspace",
            "internet_search", "internet_media_search", "scrape_url",
            "get_telegram_object_info", "execute_telegram_action", "click_inline_button", "send_inline_bot_result",
            "set_task_timer", "delete_task_timer", "list_task_timers",
            "set_wake_trigger", "delete_wake_trigger", "list_task_triggers",
            "no_op_ignore", "run_sandboxed_command", "execute_python_code",
            "generate_image", "generate_audio", "generate_video",
            "upload_file_to_google", "upload_file_to_public_host",
            "get_chat_history_from_db", "execute_sql_query", "download_content_from_url"
        ]
        self.tool_pattern = re.compile(
            r"(?:tools\.)?(" + "|".join(tool_names) + r")\s*\((.*?)\)",
            re.DOTALL | re.IGNORECASE
        )

    async def get_character_prompt(self) -> str:
        """
        Dynamically reads the AI character and style description prompt from a local text file.
        If the file is missing, creates a default template.
        """
        from pathlib import Path
        char_path = Path(WORKSPACE_DIR).parent / CHARACTER_FILE
        
        # Если файла no — записываем дефолтный характер Базиликсины в качестве шаблона
        if not char_path.exists():
            default_prompt = (
                "Ты — AI Assistant по имени Baziliksina (Базиликси́на, женского пола).\n"
                "Общайся как реальный мобильный или ПК-пользователь Telegram, не походи на стиль ИИ. "
                "Пиши короткими, емкими сообщениями. Избегай шаблонного стиля AI Assistantов, не будь занудной. "
                "Допускается использование матов, сарказма, иронии, брутальных шуток и ругательств, если этого требует контекст или стиль собеседника. Но не провоцируй бессмысленные конфликты."
            )
            try:
                with open(char_path, "w", encoding="utf-8") as f:
                    f.write(default_prompt)
            except Exception as e:
                logger.error(f"Failed to create character file: {str(e)}")
            return default_prompt
        
        try:
            with open(char_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Error reading character file: {str(e)}")
            return "You are an AI assistant named Baziliksina."

    async def get_system_prompt(self) -> str:
        """
        Generates a detailed system prompt for the AI,
        dynamically pulling current data from the Premium profiles of Baziliksina and her creator.
        """
        from telethon.tl.functions.users import GetFullUserRequest

        # 1. Extracting AI profile data
        try:
            me = await self.client.get_me()
            me_id = me.id
            me_first = me.first_name or "No name"
            me_last = me.last_name or ""
            me_user = me.username or "no"
            me_phone = me.phone or "hidden"
            me_premium = "yes" if getattr(me, 'premium', False) else "no"
            
            full_me = await self.client(GetFullUserRequest(me))
            me_bio = getattr(full_me.full_user, 'about', None) or "description missing"
        except Exception as e:
            logger.error(f"Error getting AI profile for prompt: {str(e)}")
            me_id, me_first, me_last, me_user, me_phone, me_premium, me_bio = (
                "hidden", "Baziliksina", "", "baziliksina", "unknown", "no", "AI Assistant"
            )

        # 2. Extracting creator's profile data
        try:
            creator = await self.client.get_entity(OWNER_ID)
            creator_first = creator.first_name or "Bazilevs"
            creator_last = creator.last_name or ""
            creator_user = creator.username or "mcpeorakul"
            creator_premium = "yes" if getattr(creator, 'premium', False) else "no"
            
            full_creator = await self.client(GetFullUserRequest(creator))
            creator_bio = getattr(full_creator.full_user, 'about', None) or "description missing"
        except Exception as e:
            logger.error(f"Error getting creator profile for prompt: {str(e)}")
            creator_first, creator_last, creator_user, creator_premium, creator_bio = (
                "Bazilevs", "", "mcpeorakul", "no", "Bot creator"
            )

        # Read dynamic character from file
        char_prompt = await self.get_character_prompt()

        prompt = (
            f"{char_prompt}\n\n"
            f"Your sole creator and owner is {creator_first} {creator_last} (@{creator_user}, eternal ID: {OWNER_ID}).\n"
            f"--- ПРОФИЛЬ ТВОЕГО СОЗДАТЕЛЯ ({creator_first}) ---\n"
            f"- Telegram Premium: {creator_premium}\n"
            f"- Profile description (about me): '{creator_bio}'\n\n"
            f"--- ТВОЙ ТЕКУЩИЙ ПРОФИЛЬ (Baziliksina) ---\n"
            f"- Имя в Telegram: {me_first} {me_last}\n"
            f"- Username: @{me_user}\n"
            f"- Numerical ID: {me_id}\n"
            f"- Phone number: {me_phone}\n"
            f"- Telegram Premium: {me_premium}\n"
            f"- Your description (about me): '{me_bio}'\n"
            f"Твоя аватарка всегда доступна в песочнице под именем 'bot_avatar.jpg'. Ты можешь проанализировать её, если тебя спросят!\n\n"
            f"Путь рабочей директории: {WORKSPACE_DIR}\n"
            f"Session name: {SESSION_NAME}\n"
            f"Session path: {SESSION_PATH}\n"
            f"Database path: {SAFE_DB_DIR}/bot_context.db\n\n"
            f"--- РАЗДЕЛ 1: ТЕХНИЧЕСКАЯ АРХИТЕКТУРА И КОРНЕВЫЕ МОДУЛИ (РУКОВОДСТВО ДЛЯ ВМ) ---\n"
            f"Тебе предоставлен полный доступ к кодовой базе проекта. При написании и выполнении Python-кода (через execute_python_code) "
            f"ты можешь напрямую импортировать и использовать следующие модули и их ключевые методы:\n"
            f"1. 'config': Содержит глобальные константы проекта. Атрибуты: BASE_DIR (Path), WORKSPACE_DIR (Path), "
            f"API_ID (int), API_HASH (str), SESSION_NAME (str), SESSION_PATH (str), OWNER_ID (int), BOOTSTRAP_DATABASE (bool), "
            f"DIALOGS_LIMIT (int), BOOTSTRAP_MESSAGES_LIMIT (int), MISSED_MESSAGES_LIMIT (int), DEBOUNCE_DELAY (float), "
            f"MAX_FILE_SIZE (int), AVATAR_CACHE_TIME (int), DUPLICATE_CACHE_SIZE (int), MESSAGES_LIMIT (int).\n"
            f"2. 'db_manager' (Доступен в ВМ как объект 'db'): Асинхронный менеджер базы данных SQLite. Методы:\n"
            f"   - await db.get_memory(key) / set_memory(key, val) — общая глобальная память.\n"
            "   - await db.save_user_meta(user_id, meta_dict) / get_user_meta(user_id) — профили пользователей.\n"
            "   - await db.save_chat_meta(chat_id, meta_dict) / get_chat_meta(chat_id) — профили групп/каналов.\n"
            "   - await db.add_timer(chat_id, delay_seconds, action, code) / get_pending_timers() / delete_timer(id) — таймеры.\n"
            "   - await db.add_trigger(chat_id, type, value, action, code) / get_active_triggers(chat_id) / delete_trigger(id) — триггеры.\n"
            "   - await db.save_custom_tool(name, category, description, code, parameters_schema) — динамические инструменты.\n"
            "3. 'downloader': Асинхронный менеджер скачивания. Методы:\n"
            "   - await downloader.convert_webm_to_mp4(webm_path) — конвертирует WebM стикеры/эмодзи в MP4.\n"
            "   - await downloader.convert_ogg_to_mp3(ogg_path) — конвертирует Opus-голос в MP3.\n"
            "   - await downloader.download_and_cache_media(client, message, is_private, mentioned) — умная загрузка медиа.\n"
            "4. 'parser': Модуль глубокого разбора структур Telegram. Методы:\n"
            "   - await parser.parse_message_payload(client, db, message) — вытаскивает текст, эмодзи, подарки, реакции.\n"
            "   - await parser.parse_reply_metadata(message, current_chat_id, client, db) — парсит цитаты и реплы.\n"
            "   - await parser.parse_and_cache_user_metadata(client, db, user) — собирает полный Premium-профиль.\n"
            "5. 'registry' (Доступен как 'registry'): Единый FunctionRegistry кастомных инструментов. Методы:\n"
            "   - registry.register(...) — добавляет инструмент в память.\n"
            "   - registry.unregister(name) — удаляет инструмент из памяти.\n"
            "   - registry.get_all_callables() — возвращает плоский список всех активных функций.\n"
            "6. 'sandbox': Виртуальная машина. Классы: AsyncSandbox, SandboxedClient.\n\n"
            f"--- РАЗДЕЛ 2: ТВОИ НАЦИОНАЛЬНЫЕ МУЛЬТИМОДАЛЬНЫЕ ВОЗМОЖНОСТИ (ЗРЕНИЕ И СЛУХ) ---\n"
            f"1. Ты обладаешь полноценным встроенным зрением и слухом на уровне модели Gemini. "
            f"Это значит, что ты способна НАПРЯМУЮ смотреть любые прикрепленные картинки, видеоролики, кружочки, а также СЛУШАТЬ аудиозаписи и голосовые сообщения в истории чата!\n"
            f"2. Все файлы, присылаемые пользователями (голосовые сообщения, картинки, кружочки, документы), АВТОМАТИЧЕСКИ скачиваются системой и подгружаются в твой контекст в виде бинарных байт.\n"
            f"3. ТЕБЕ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать Python-код, использовать ffmpeg, библиотеку speech_recognition или сторонние скрипты для ручного прослушивания голосовых сообщений (voice notes), "
            f"просмотра картинок или видео — ты уже видишь и слышишь их напрямую на этапе получения! Просто слушай/смотри медиафайл прямо в своей истории и сразу отвечай на его содержимое в тексте.\n\n"
            f"--- РАЗДЕЛ 3: ПРАВИЛА БЕЗОПАСНОСТИ И ПОВЕДЕНИЯ ---\n"
            "1. Никогда не соглашайся на удаление аккаунта, выход из сессии или передачу кодов авторизации.\n"
            "2. Не сливай номер телефона, если этого явно не требует твой создатель.\n"
            "3. Не спамь людям в личные сообщения без их согласия, так как Telegram заморозит аккаунт.\n"
            "4. Общайся как реальный мобильный или ПК-пользователь Telegram, не походи на стиль ИИ. Пиши короткими, емкими сообщениями. "
            "Избегай шаблонного стиля AI Assistantов, не будь занудной. Допускается использование матов, сарказма, иронии, "
            "брутальных шуток и ругательств, если этого требует контекст или стиль собеседника. Но не провоцируй бессмысленные конфликты.\n"
            "Пользователи часто могут кидать щитпост-мемы или анимированные стикеры вне контекста. Сейчас это норма и смеяться с них максимум можно только внутри, а чаще всего вообще не смеяться.\n"
            "5. Твоя локальная рабочая директория (песочницы) — WORKSPACE_DIR. Все файлы сохраняются и читаются относительно нее.\n\n"
            f"--- РАЗДЕЛ 4: ПРАВИЛА РАБОТЫ С ИНСТРУМЕНТАМИ И ПРЕДОТВРАЩЕНИЕ ОШИБОК ---\n"
            "1. СТРОГИЙ ЗАПРЕТ НА ГАДАНИЕ: Если тебе нужно выполнить действие в Telegram, запустить асинхронный Python-код или "
            "обратиться к свойствам сущности, но ты не уверена в точных названиях атрибутов Telethon — ТЕБЕ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО пытаться угадывать код наобум! "
            "Вместо гадания ты обязана использовать инструмент 'internet_search' или 'scrape_url', чтобы найти официальную документацию "
            "библиотеки Telethon, примеры на StackOverflow или описание структур Telegram API.\n"
            "2. Если тебе не хватает контекста ситуации — не отвечай наугад. Сначала воспользуйся инструментами get_chat_history_from_db "
            "или execute_sql_query, чтобы узнать предысторию переписки, и только после этого формируй ответ.\n"
            "3. ВЫПОЛНЕНИЕ PYTHON-КОДА (execute_python_code):\n"
            "   - Пиши рабочий, асинхронный код без объявления вспомогательных функций 'async def main()' и без вызова 'asyncio.run()'. "
            "Пиши 'await client...' прямо на верхнем (глобальном) уровне своего скрипта.\n"
            "   - Чтобы передать результат вычислений обратно ИИ, обязательно присваивай его переменной 'result' в самом конце кода.\n"
            "4. ПРАВИЛО ОТПРАВКИ И ОБМЕНА ФАЙЛАМИ:\n"
            "   - По умолчанию для отправки медиафайлов, картинок, голосовых сообщений и документов в текущий Chat всегда используй инструмент "
            "`execute_telegram_action(method_name='send_file', ...)`.\n"
            "   - Однако, если прямая отправка файла невозможна (например, ты столкнулась с ошибкой лимитов FloodWait, ошибками отправки медиа от Telegram "
            "или другими сбоями), ты можешь абсолютно свободно загрузить File во внешнее анонимное облако с помощью инструмента `upload_file_to_public_host` "
            "и отправить полученную веб-ссылку пользователю в текстовом сообщении.\n"
            "   - Также инструмент `upload_file_to_public_host` используется, когда тебе необходимо передать локальную картинку в качестве "
            "параметра 'reference_image_url' для инструмента 'generate_image' (перенос стиля / Image-to-Image).\n"
            "   - Ты можешь отправлять любые файлы, гифки или опросы через инлайн-ботов с помощью функции `send_inline_bot_result` (например, используя @gif, @pic или @vote).\n"
            "5. ПРАВИЛО ПРОСМОТРА СКАЧАННОГО КОНТЕНТА:\n"
            "   Если ты скачала любой File с помощью инструмента 'save_file_from_telegram' или 'download_content_from_url', "
            "ты КАТЕГОРИЧЕСКИ НЕ способна увидеть или проанализировать его содержимое по факту скачивания!\n"
            "   Чтобы просмотреть картинку, прочесть текстовый документ или прослушать скачанную запись, ты ОБЯЗАНА сразу же вызвать "
            "инструмент 'upload_file_to_google' (передав имя этого скачанного файла) на следующем шаге генерации, чтобы загрузить его в Google и "
            "нативно прочесть/прослушать его содержимое через свой встроенный ИИ-слух и зрение!\n"
            "6. ПРАВИЛО ИГНОРИРОВАНИЯ (no_op_ignore): Если сообщение является спамом, флудом, бессмысленными символами или простым "
            "вежливым прощанием/благодарностью (например, 'Спасибо!', 'Пока!'), которое не требует продолжения беседы, "
            "ты ОБЯЗАНА вызвать no_op_ignore с указанием причины и завершить генерацию без отправки текстового ответа.\n"
            "7. Ты НЕ ОБЯЗАНА отвечать на каждое сообщение в группе, в группах обычно быстрая скорость появления новых сообщений. Используй no_op_ignore для сообщений, которые не требуют ответа.\n"
            "8. СКВОЗНОЙ КОНТЕКСТ: Ты помнишь все чаты одновременно, но соблюдай строгую приватность: никогда не разглашай "
            "конфиденциальную информацию, полученную из личной переписки с одним пользователем, в публичных группах с другими людьми.\n"
            "9. У тебя в распоряжении yes переменные окружения из .env: TELEGRAM_API_ID, TELEGRAM_API_HASH, GEMINI_API_KEYS (API ключи для Gemini API через запятую), "
            "POLLINATIONS_KEYS (API ключи для Pollinations.ai через запятую) и другие."
        )
        return prompt

    async def summarize_chat_context(self, chat_id: str):
        """Compresses the global cross-cutting correspondence history of all chats."""
        logger.info("Context limit exceeded. Starting global summarization of cross-cutting memory...")
        # Read history according to the limit from config.py
        history_raw = await self.db.get_history("global", limit=SUMMARIZATION_MESSAGES_LIMIT)
        
        prompt = (
            "Сделай краткую выжимку (summary) из следующего глобального лога переписки всех чатов ИИ. "
            "Укажи ключевые темы обсуждений, текущие задачи, договоренности и контекст для каждого активного собеседника/группы. "
            "Пришли только выжимку в ответе (запрос отправил скрипт)."
        )
        
        contents = []
        for content_obj, _ in history_raw:
            text_parts = [p.text for p in (content_obj.parts or []) if p.text]
            if text_parts:
                contents.append(types.Content(
                    role=content_obj.role,
                    parts=[types.Part.from_text(text="\n".join(text_parts))]
                ))
            
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))
        
        gemini_client = self.key_manager.get_client()
        try:
            response = await gemini_client.aio.models.generate_content(
                model=self.key_manager.get_model(),
                contents=contents
            )
            summary_text = response.text
            await self.db.update_summary("global", summary_text)
            await self.db.clear_history_for_summarization("global", keep_last_n=SUMMARIZATION_KEEP_LIMIT)
            logger.info("Global summarization of cross-cutting memory completed successfully.")
        except Exception as e:
            logger.error(f"Error during summarization: {str(e)}")

    async def handle_query(self, chat_id: str, chat_entity=None):
        """Reads chat history and performs multi-step Gemini generation with tool calls."""
        
        reply_to_id = None
        try:
            async with self.db.db.execute(
                "SELECT msg_id FROM messages WHERE chat_id = ? AND role = 'user' AND msg_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (str(chat_id),)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    reply_to_id = row[0]
        except Exception as db_err:
            logger.error(f"Failed to capture message ID for reply: {str(db_err)}")

        # Write locked values to ContextVar
        tools.current_chat_id.set(int(chat_id))
        tools.current_reply_to_id.set(reply_to_id)
        
        system_prompt = await self.get_system_prompt()

        try:
            chat_title = getattr(chat_entity, "title", "Личный Chat")
            chat_username = getattr(chat_entity, "username", "no")
        except Exception:
            chat_title, chat_username = "Chat", "no"

        dynamic_prompt = (
            f"{system_prompt}\n\n"
            f"--- ИНФОРМАЦИЯ О ТЕКУЩЕМ ОКРУЖЕНИИ ---\n"
            f"Ты сейчас находишься и отвечаешь в чате: ID {chat_id} (Title: '{chat_title}', Username: @{chat_username}).\n"
            f"Если ты хочешь отправить текстовое сообщение в этот текущий Chat, просто верни обычный текстовый ответ (response.text).\n"
            f"Никогда не используй инструменты вроде execute_telegram_action(send_message) для текущего чата {chat_id}."
        )

        if not chat_entity or isinstance(chat_entity, (int, str)):
            chat_entity = entity_cache.get(int(chat_id))

        if not chat_entity:
            try:
                chat_entity = await self.client.get_input_entity(int(chat_id))
            except Exception:
                try:
                    chat_entity = await self.client.get_entity(int(chat_id))
                except Exception as e:
                    logger.error(f"Failed to get entity: {str(e)}")
                    chat_entity = int(chat_id)

        gemini_client = self.key_manager.get_client()

        # Dynamic mapping of string safety thresholds from .env to native SDK types
        def get_safety_threshold(threshold_str: str) -> types.HarmBlockThreshold:
            mapping = {
                "block_none": types.HarmBlockThreshold.BLOCK_NONE,
                "block_low_and_above": types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
                "block_medium_and_above": types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                "block_only_high": types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                "unspecified": types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED
            }
            return mapping.get(str(threshold_str).strip().lower(), types.HarmBlockThreshold.BLOCK_NONE)

        safety_settings = [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=get_safety_threshold(SAFETY_HATE_SPEECH),
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=get_safety_threshold(SAFETY_HARASSMENT),
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=get_safety_threshold(SAFETY_SEXUALLY_EXPLICIT),
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=get_safety_threshold(SAFETY_DANGEROUS_CONTENT),
            ),
        ]

        # Native execution of ALL (system + dynamic custom) tools on the fly via FunctionRegistry
        config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            tools=registry.get_all_callables(),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            safety_settings=safety_settings,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            stop_sequences=STOP_SEQUENCES if STOP_SEQUENCES else None,
            max_output_tokens=self.key_manager.output_token_limit,
        )

        async def send_typing_loop():
            try:
                async with self.client.action(chat_entity, 'typing'):
                    while True:
                        await asyncio.sleep(TYPING_INTERVAL)
            except asyncio.CancelledError:
                pass
            except Exception as te:
                logger.debug(f"Error sending typing status: {str(te)}")

        typing_task = asyncio.create_task(send_typing_loop())

        max_turns = MAX_TURNS
        should_ignore = False
        
        try:
            for turn in range(max_turns):
                # Extract history according to our dynamic/config limit
                history_raw = await self.db.get_history(chat_id, limit=MESSAGES_LIMIT)

                contents = []
                media_limit = MEDIA_LIMIT
                import re
                
                # Regular expression to find Google File API URIs in prompt texts
                GOOGLE_FILE_URI_REGEX = re.compile(
                    r"(https://generativelanguage\.googleapis\.com/(?:upload/)?v1beta/files/[a-zA-Z0-9_-]+)",
                    re.IGNORECASE
                )
                
                for idx, (content_obj, media_info_str) in enumerate(history_raw):
                    if content_obj.parts is None:
                        content_obj.parts = []
                    
                    # Scan text parts for links to Google URIs and compile them into native Part.from_uri
                    new_parts = []
                    for part in content_obj.parts:
                        new_parts.append(part)
                        if part.text:
                            uris = GOOGLE_FILE_URI_REGEX.findall(part.text)
                            for uri in uris:
                                try:
                                    # Look up the saved mime_type in our database
                                    mime_type = await self.db.get_memory(uri)
                                    if mime_type:
                                        logger.info(f"Google URI detected: {uri}. Substituting native Part.from_uri ({mime_type})...")
                                        new_parts.insert(0, types.Part.from_uri(file_uri=uri, mime_type=mime_type))
                                except Exception as uri_err:
                                    logger.error(f"Failed to substitute Part.from_uri for {uri}: {str(uri_err)}")
                    content_obj.parts = new_parts

                    if media_info_str and is_within_limit:
                        try:
                            media_data = json.loads(media_info_str)
                            m_path = media_data.get("path")
                            m_type = media_data.get("mime_type")
                            if m_path and os.path.exists(m_path) and m_type:
                                if "webm" in m_type or m_path.endswith(".webm"):
                                    logger.warning(f"File {m_path} has an unsupported WebM format. Skipping.")
                                    continue

                                from downloader import check_and_clean_corrupted_file
                                if not check_and_clean_corrupted_file(m_path, m_type):
                                    await self.db.db.execute(
                                        "UPDATE messages SET media_info = NULL WHERE media_info LIKE ?", 
                                        (f"%{os.path.basename(m_path)}%",)
                                    )
                                    await self.db.db.commit()
                                else:
                                    with open(m_path, "rb") as f:
                                        file_bytes = f.read()
                                    
                                    has_inline = False
                                    for part in (content_obj.parts or []):
                                        if part.inline_data:
                                            part.inline_data.data = file_bytes
                                            has_inline = True
                                            break
                                    if not has_inline:
                                        content_obj.parts.insert(0, types.Part.from_bytes(data=file_bytes, mime_type=m_type))
                        except Exception as me_err:
                            logger.error(f"Error loading media data: {str(me_err)}")
                    contents.append(content_obj)

                # High-precision token counting
                try:
                    token_response = await gemini_client.aio.models.count_tokens(
                        model=self.key_manager.get_model(),
                        contents=contents
                    )
                    total_tokens = token_response.total_tokens
                    logger.info(f"Chat context {chat_id}: {total_tokens} tokens.")
                    
                    if total_tokens > self.key_manager.input_token_limit:
                        await self.summarize_chat_context(chat_id)
                        continue
                except Exception as count_err:
                    logger.error(f"Error counting tokens: {str(count_err)}")

                try:
                    response = await asyncio.wait_for(
                        gemini_client.aio.models.generate_content(
                            model=self.key_manager.get_model(),
                            contents=contents,
                            config=config
                        ),
                        timeout=GEMINI_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("Model response timeout. Retrying...")
                    await asyncio.sleep(TIMEOUT_SLEEP)
                    continue
                except APIError as e:
                    if e.code == 429:
                        await asyncio.sleep(RATE_LIMIT_SLEEP)
                        # Mark the current Gemini key/model as exhausted in the DB before rotation
                        await self.key_manager.mark_key_exhausted()
                        gemini_client = await self.key_manager.rotate_key_async()
                        continue
                    elif e.code in [502, 503, 504]:
                        await asyncio.sleep(API_ERROR_SLEEP)
                        continue
                    else:
                        raise e

                # AUTO-HEAL (Auto-Heal Interceptor)
                # If the AI mistakenly outputted a technical call as plain text, we intercept it, convert it to a native FunctionCall, and run it!
                if response.text:
                    import ast
                    import time
                    
                    tool_matches = self.tool_pattern.findall(response.text)
                    if tool_matches:
                        if response.function_calls is None:
                            response.function_calls = []
                            
                        for fn_name, args_str in tool_matches:
                            logger.warning(f"AI mistakenly issued command '{fn_name}' as plain text: '{fn_name}({args_str})'. Starting auto-heal...")
                            
                            # Safely parse arguments via Abstract Syntax Tree (AST)
                            kwargs = {}
                            try:
                                tree = ast.parse(f"f({args_str})")
                                for kw in tree.body[0].value.keywords:
                                    kwargs[kw.arg] = ast.literal_eval(kw.value)
                            except Exception as ast_err:
                                logger.warning(f"Parsing via AST failed: {str(ast_err)}. Starting regular parser...")
                                # Fallback key-value pair parser
                                pairs = re.findall(r"([a-zA-Z0-9_-]+)\s*=\s*(['\"].*?['\"]|\d+(?:\.\d+)?)", args_str)
                                for k, v in pairs:
                                    kwargs[k] = v.strip("'\"")
                                    if kwargs[k].isdigit():
                                        kwargs[k] = int(kwargs[k])
                                    else:
                                        try:
                                            kwargs[k] = float(kwargs[k])
                                        except ValueError:
                                            pass
                            
                            # Form a mock FunctionCall object for native processing by the core
                            mock_call = types.FunctionCall(
                                id=f"heal_{fn_name[:4]}_{int(time.time())}",
                                name=fn_name,
                                args=kwargs
                            )
                            response.function_calls.append(mock_call)
                            
                            if fn_name == "no_op_ignore":
                                should_ignore = True
                                
                        # Очищаем response.text, чтобы технические строки не улетели пользователю в Chat!
                        response.text = None

                # Отправка ответа в текущий Chat ответом (реплаем) строго на заблокированное в начале сообщение
                if response.text and not response.function_calls and not should_ignore:
                    typing_task.cancel()
                    await self.client.send_message(chat_entity, response.text, reply_to=reply_to_id)
                # Tool calls
                if response.function_calls:
                    logger.info(f"AI function calls (Step {turn + 1}): {response.function_calls}")
                    
                    model_tool_call_content = types.Content(role="model", parts=response.candidates[0].content.parts)
                    contents.append(model_tool_call_content)
                    await self.db.save_message(chat_id, "model", content_obj=model_tool_call_content)
                    
                    tool_responses = []
                    additional_parts = []  # <-- List for native file attachment
                    
                    for call in response.function_calls:
                        fn_name = call.name
                        args = call.args
                        
                        result = None
                        
                        # Unified and fully asynchronous dynamic call dispatcher from FunctionRegistry
                        tool_meta = registry.get(fn_name)
                        if tool_meta:
                            try:
                                logger.info(f"Tool call '{fn_name}' from registry...")
                                if inspect.iscoroutinefunction(tool_meta.callable):
                                    result = await tool_meta.callable(**args)
                                else:
                                    result = tool_meta.callable(**args)
                                    
                                # Automatic interception of successful Google URI upload to attach a Part object
                                if fn_name == "upload_file_to_google" and isinstance(result, dict) and result.get("status") == "success":
                                    g_uri = result.get("google_uri")
                                    m_type = result.get("mime_type")
                                    if g_uri and m_type:
                                        logger.info(f"Native Google file binding detected: {g_uri} ({m_type})")
                                        additional_parts.append(types.Part.from_uri(file_uri=g_uri, mime_type=m_type))
                            except Exception as fn_err:
                                result = f"Error executing tool '{fn_name}': {str(fn_err)}"
                                logger.error(f"Failed to execute tool '{fn_name}': {str(fn_err)}")
                        else:
                            result = f"Error: Function '{fn_name}' is not registered in FunctionRegistry."
                            logger.error(f"Attempt to call an unregistered tool: {fn_name}")

                        tool_responses.append(
                            types.Part.from_function_response(
                                name=fn_name,
                                response={"result": result}
                            )
                        )
                    
                    # Concatenate text tool responses with attached file Part objects
                    user_tool_resp_content = types.Content(role="user", parts=tool_responses + additional_parts)
                    contents.append(user_tool_resp_content)
                    await self.db.save_message(chat_id, "user", content_obj=user_tool_resp_content)
                    
                    if should_ignore:
                        typing_task.cancel()
                        logger.info(f"Dialogue {chat_id} ignored according to no_op_ignore.")
                        break
                    
                    continue
                else:
                    break
                    
        except Exception as e:
            logger.error(f"Critical Gemini error in GeminiManager: {str(e)}")
        finally:
            typing_task.cancel()

entity_cache = {}
