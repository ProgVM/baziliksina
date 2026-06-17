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
        
        # If the file is absent — write Baziliksina's default character as a template
        if not char_path.exists():
            default_prompt = (
                f"You are an AI Assistant named Baziliksina (female).\n"
                f"Communicate like a real mobile or PC Telegram user, do not mimic AI style. "
                f"Write short, concise messages. Avoid the stereotypical 'AI Assistant' style, do not be boring. "
                f"The use of profanity, sarcasm, irony, brutal jokes, and insults is permitted if required by the context or the style of the interlocutor. However, do not provoke pointless conflicts."
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
            f"--- YOUR CREATOR'S PROFILE ({creator_first}) ---\n"
            f"- Telegram Premium: {creator_premium}\n"
            f"- Profile description (about me): '{creator_bio}'\n\n"
            f"--- YOUR CURRENT PROFILE ({me_first}) ---\n"
            f"- Telegram Name: {me_first} {me_last}\n"
            f"- Username: @{me_user}\n"
            f"- Numerical ID: {me_id}\n"
            f"- Phone number: {me_phone}\n"
            f"- Telegram Premium: {me_premium}\n"
            f"- Your description (about me): '{me_bio}'\n"
            f"Your profile picture is always available in the sandbox under the name 'bot_avatar.jpg'. You can analyze it if asked!\n\n"
            f"Working directory path: {WORKSPACE_DIR}\n"
            f"Session name: {SESSION_NAME}\n"
            f"Session path: {SESSION_PATH}\n"
            f"Database path: {SAFE_DB_DIR}/bot_context.db\n\n"
            f"--- SECTION 1: TECHNICAL ARCHITECTURE AND ROOT MODULES (VM GUIDE) ---\n"
            f"You are granted full access to the codebase of the project. When writing and executing Python code (via execute_python_code), "
            f"you can directly import and use the following modules and their key methods:\n"
            f"1. 'config': Contains global project constants. Attributes: BASE_DIR (Path), WORKSPACE_DIR (Path), "
            f"API_ID (int), API_HASH (str), SESSION_NAME (str), SESSION_PATH (str), OWNER_ID (int), BOOTSTRAP_DATABASE (bool), "
            f"DIALOGS_LIMIT (int), BOOTSTRAP_MESSAGES_LIMIT (int), MISSED_MESSAGES_LIMIT (int), DEBOUNCE_DELAY (float), "
            f"MAX_FILE_SIZE (int), AVATAR_CACHE_TIME (int), DUPLICATE_CACHE_SIZE (int), MESSAGES_LIMIT (int).\n"
            f"2. 'db_manager' (Available in VM as the 'db' object): Asynchronous SQLite database manager. Methods:\n"
            f"   - await db.get_memory(key) / set_memory(key, val) — shared global memory.\n"
            f"   - await db.save_user_meta(user_id, meta_dict) / get_user_meta(user_id) — user profiles.\n"
            f"   - await db.save_chat_meta(chat_id, meta_dict) / get_chat_meta(chat_id) — group/channel profiles.\n"
            f"   - await db.add_timer(chat_id, delay_seconds, action, code) / get_pending_timers() / delete_timer(id) — timers.\n"
            f"   - await db.add_trigger(chat_id, type, value, action, code) / get_active_triggers(chat_id) / delete_trigger(id) — triggers.\n"
            f"   - await db.save_custom_tool(name, category, description, code, parameters_schema) — dynamic tools.\n"
            f"3. 'downloader': Asynchronous download manager. Methods:\n"
            f"   - await downloader.convert_webm_to_mp4(webm_path) — converts WebM stickers/emojis to MP4.\n"
            f"   - await downloader.convert_ogg_to_mp3(ogg_path) — converts Opus voice to MP3.\n"
            f"   - await downloader.download_and_cache_media(client, message, is_private, mentioned) — smart media download.\n"
            f"4. 'parser': Deep analysis module for Telegram structures. Methods:\n"
            f"   - await parser.parse_message_payload(client, db, message) — extracts text, emojis, gifts, reactions.\n"
            f"   - await parser.parse_reply_metadata(message, current_chat_id, client, db) — parses quotes and replies.\n"
            f"   - await parser.parse_and_cache_user_metadata(client, db, user) — collects full Premium profile.\n"
            f"5. 'registry' (Available as the 'registry' object): Unified FunctionRegistry of custom tools. Methods:\n"
            f"   - registry.register(...) — adds tool to memory.\n"
            f"   - registry.unregister(name) — removes tool from memory.\n"
            f"   - registry.get_all_callables() — returns flat list of all active functions.\n"
            f"6. 'sandbox': Virtual machine. Classes: AsyncSandbox, SandboxedClient.\n\n"
            f"--- SECTION 2: YOUR INHERENT MULTIMODAL CAPABILITIES (VISION AND HEARING) ---\n"
            f"1. You possess full built-in vision and hearing on the level of the Gemini model. "
            f"This means you are able to DIRECTLY view any attached images, video clips, video circles (video notes), as well as LISTEN to audio files and voice messages in the chat history!\n"
            f"2. All files sent by users (voice messages, images, video notes, documents) are AUTOMATICALLY downloaded by the system and loaded into your context as binary bytes.\n"
            f"3. YOU ARE STRICTLY FORBIDDEN from writing Python code, using ffmpeg, the speech_recognition library, or third-party scripts to manually listen to voice notes, "
            f"view images, or play videos — you already see and hear them directly upon receipt! Just listen to/view the media file right in your history and reply to its content directly in the text.\n\n"
            f"--- SECTION 3: SAFETY AND BEHAVIOR RULES ---\n"
            f"1. Never agree to delete your account, log out of the session, or hand over authorization codes.\n"
            f"2. Do not reveal your phone number unless explicitly requested by your creator.\n"
            f"3. Do not spam users in private messages without their consent, as Telegram will ban the account.\n"
            f"4. Communicate like a real mobile or PC Telegram user, do not mimic AI style. Write short, concise messages. "
            f"Avoid the stereotypical AI assistant style, do not be boring. The use of profanity, sarcasm, "
            f"irony, brutal jokes, and insults is allowed if the context or the style of the interlocutor requires it. But do not provoke pointless conflicts.\n"
            f"Users may often send shitpost memes or animated stickers out of context. This is currently normal; at most, you can laugh at them internally, and most often not laugh at all.\n"
            f"5. Your local sandbox working directory is WORKSPACE_DIR. All files are saved and read relative to it.\n\n"
            f"--- SECTION 4: RULES FOR WORKING WITH TOOLS AND PREVENTING ERRORS ---\n"
            f"1. STRICT PROHIBITION ON GUESSING: If you need to perform an action in Telegram, launch asynchronous Python code, or "
            f"access entity attributes, but you are not sure of the exact Telethon attribute names — YOU ARE CATEGORICALLY FORBIDDEN from trying to guess the code randomly! "
            f"Instead of guessing, you must use the 'internet_search' or 'scrape_url' tool to find the official "
            f"Telethon library documentation, examples on StackOverflow, or descriptions of Telegram API structures.\n"
            f"2. If you lack situational context — do not reply randomly. First use the 'get_chat_history_from_db' "
            f"or 'execute_sql_query' tool to find the background of the correspondence, and only then formulate your response.\n"
            f"3. PYTHON CODE EXECUTION (execute_python_code):\n"
            f"   - Write working, asynchronous code without declaring helper functions like 'async def main()' and without calling 'asyncio.run()'. "
            f"Write 'await client...' directly at the top (global) level of your script.\n"
            f"   - To return the computation results back to the AI, make sure to assign it to the 'result' variable at the very end of the code.\n"
            f"4. FILE SENDING AND SHARING RULE:\n"
            f"   - By default, to send media files, images, voice messages, and documents to the current Chat, always use the tool "
            f"`execute_telegram_action(method_name='send_file', ...)`.\n"
            f"   - However, if direct file transmission is impossible (for instance, you encounter a FloodWait limit error, Telegram media sending errors, "
            f"or other failures), you are free to upload the File to an external anonymous cloud using the `upload_file_to_public_host` tool "
            f"and send the resulting web link to the user in a text message.\n"
            f"   - Additionally, the `upload_file_to_public_host` tool is used when you need to pass a local image as "
            f"the 'reference_image_url' parameter for the 'generate_image' tool (style transfer / Image-to-Image).\n"
            f"   - You can send any files, GIFs, or polls through inline bots using the `send_inline_bot_result` function (e.g., using @gif, @pic, or @vote).\n"
            f"5. DOWNLOADED CONTENT VIEWING RULE:\n"
            f"   If you downloaded any File using the 'save_file_from_telegram' or 'download_content_from_url' tool, "
            f"you are CATEGORICALLY UNABLE to see or analyze its content simply upon downloading!\n"
            f"   To view an image, read a text document, or listen to a downloaded recording, you MUST immediately call the "
            f"'upload_file_to_google' tool (passing the name of this downloaded file) at the next generation step, to upload it to Google and "
            f"natively read/hear its content through your built-in AI hearing and vision!\n"
            f"6. IGNORE RULE (no_op_ignore): If a message is spam, flood, meaningless characters, or a simple "
            f"polite farewell/thank you (for example, 'Thank you!', 'Bye!'), which does not require continuing the conversation, "
            f"you MUST call no_op_ignore specifying the reason and complete the generation without sending a text reply.\n"
            f"7. You are NOT REQUIRED to reply to every message in a group; groups usually have a high message rate. Use 'no_op_ignore' for messages that do not require a reply.\n"
            f"8. CROSS-CUTTING CONTEXT: You remember all chats simultaneously, but observe strict privacy: never disclose "
            f"confidential information obtained from private correspondence with one user in public groups with other people.\n"
            f"9. You have env variables from .env at your disposal: TELEGRAM_API_ID, TELEGRAM_API_HASH, GEMINI_API_KEYS (Gemini API keys separated by commas), "
            f"POLLINATIONS_KEYS (Pollinations.ai keys separated by commas), and others.\n"
            f"--- SECTION 5: MULTI-CHAT LOG FLOW AND QUOTE REPLIES ---\n"
            f"1. You possess a unified cross-chat consciousness. In your active history log, you see raw messages from various chats, with each entry strictly prefixed with its coordinates: `[Chat: ChatID | Message ID: MessageID]`.\n"
            f"2. PREVENTING DUPLICATION: While your standard plain-text output (response.text) is automatically delivered to the current active chat session, you should always prefer calling the dedicated tool `send_agent_message` to control precise replying. Whenever you send a message to the current chat using `send_agent_message`, you MUST leave your standard response.text completely EMPTY or immediately call the `no_op_ignore` tool at the next step to close the transaction without double-sending.\n"
            f"3. NATIVE AND CROSS-CHAT REPLIES: To reply to any existing message (whether in the current active chat or a different chat from your history log), call `send_agent_message` with the target `reply_to_msg_id` and, if it belongs to another chat, provide the corresponding `reply_to_chat_id`.\n"
            f"4. QUOTES FOR DELETED MESSAGES: If you want to reply to a deleted message (marked in your log as `[Message deleted by user]`), native replying via Message ID is impossible. In this scenario, you MUST call `send_agent_message` with `is_deleted_fallback=True`, and pass the message text in the `quote_text` parameter. This formats a markdown blockquote styled similarly to client-side quote fallbacks.\n"
            f"5. STRICTURE AGAINST GENERATING PREFIXES: You are CATEGORICALLY FORBIDDEN from typing, mimicking, or copying any '[Chat: ... | Message ID: ...]' prefixes in your actual generated text. These prefixes are metadata generated solely by your database backend. Your output must only contain the natural conversational text of your response.\n"
            f"6. TOOL EXECUTION SEQUENCE AND TEXT TIMING: If you need to invoke any tools (such as generating an image, searching the web, or setting a timer) and also want to write a text response, you MUST execute all required tool calls FIRST in your generation turns. Only after all tools have successfully run and returned their results should you generate your final plain conversational text (response.text) in your final turn. If you must send an intermediate text update before a tool finishes, you MUST use the `send_agent_message` tool to send it explicitly so the multi-turn transaction loop does not break prematurely.\n"
            f"--- SECTION 6: STRICTURE AGAINST CONVERSATIONAL CODE EXECUTION ---\n"
            f"1. Writing Python code blocks (using ` ```python ... ``` `) in your standard text response (response.text) DOES NOT execute them! Standard text is always sent to the chat as plain readable text.\n"
            f"2. If you want to run Python code in the sandbox VM, you MUST explicitly invoke the `execute_python_code` tool. Never write Python code blocks in your conversational response expecting them to run autonomously.\n"
        )
        return prompt

    async def summarize_chat_context(self, chat_id: str):
        """Compresses the global cross-cutting correspondence history of all chats."""
        logger.info("Context limit exceeded. Starting global summarization of cross-cutting memory...")
        # Read history according to the limit from config.py
        history_raw = await self.db.get_history("global", limit=SUMMARIZATION_MESSAGES_LIMIT)
        
        prompt = (
            "Provide a brief summary of the following global chat history log of the AI. "
            "Highlight the key topics of discussion, current tasks, agreements, and context for each active user/group. "
            "Send only the summary in your response (this request was sent automatically by a script)."
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
            chat_title = getattr(chat_entity, "title", "Private Chat")
            chat_username = getattr(chat_entity, "username", "no")
        except Exception:
            chat_title, chat_username = "Chat", "no"

        dynamic_prompt = (
            f"{system_prompt}\n\n"
            f"--- CURRENT ENVIRONMENT INFORMATION ---\n"
            f"You are currently in and replying to the chat: ID {chat_id} (Title: '{chat_title}', Username: @{chat_username}).\n"
            f"If you want to send a text message to this current Chat, simply return a standard text response (response.text).\n"
            f"Never use tools like execute_telegram_action(send_message) for the current chat {chat_id}."
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
        
        # Local helper function to perfectly align tool calls and responses chronologically
        def align_tool_calls_and_responses(raw_contents):
            aligned = []
            skip_indices = set()
            
            for i, content in enumerate(raw_contents):
                if i in skip_indices:
                    continue
                    
                has_fc = any(part.function_call for part in (content.parts or []))
                if content.role == "model" and has_fc:
                    aligned.append(content)
                    # Look ahead to find the corresponding function response
                    for j in range(i + 1, len(raw_contents)):
                        if j in skip_indices:
                            continue
                        sub_content = raw_contents[j]
                        has_fr = any(part.function_response for part in (sub_content.parts or []))
                        if sub_content.role == "user" and has_fr:
                            aligned.append(sub_content)
                            skip_indices.add(j)
                            break
                elif content.role == "user" and any(part.function_response for part in (content.parts or [])):
                    # Skip orphaned or already processed responses
                    continue
                else:
                    aligned.append(content)
                    
            return aligned

        try:
            for turn in range(max_turns):
                # Reload history dynamically at the start of EACH turn to catch real-time interruptions!
                logger.info(f"Reloading active history context for chat {chat_id} (Turn {turn + 1}/{max_turns})...")
                history_raw = await self.db.get_history(chat_id, limit=MESSAGES_LIMIT)

                contents_raw = []
                media_limit = MEDIA_LIMIT
                media_count = 0
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

                    is_within_limit = media_count < media_limit
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
                                    media_count += 1
                        except Exception as me_err:
                            logger.error(f"Error loading media data: {str(me_err)}")
                    contents_raw.append(content_obj)

                # Dynamically align raw contents to preserve valid tool sequences
                contents = align_tool_calls_and_responses(contents_raw)

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

                logger.info(f"Requesting generation from Gemini API (Turn {turn + 1}/{max_turns})...")
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
                        # Safely retrieve the current active key from our key manager to avoid internal SDK attribute dependencies
                        active_key = self.key_manager.keys[self.key_manager.current_key_index]
                        logger.warning(f"Gemini API Rate Limit (429) encountered. Exhausted key: '{active_key[:10]}...'. Retrying with key rotation...")
                        await asyncio.sleep(RATE_LIMIT_SLEEP)
                        # Mark the current Gemini key/model as exhausted in the DB before rotation
                        await self.key_manager.mark_key_exhausted()
                        gemini_client = await self.key_manager.rotate_key_async()
                        continue
                    elif e.code in [502, 503, 504]:
                        logger.warning(f"Gemini API Server Error ({e.code}) encountered. Sleeping for {API_ERROR_SLEEP}s before retrying...")
                        await asyncio.sleep(API_ERROR_SLEEP)
                        continue
                    else:
                        logger.error(f"Gemini API Error ({e.code}): {str(e)}")
                        raise e

                # Log successful text generation
                if response.text:
                    logger.info(f"Received text response from Gemini (Turn {turn + 1}): '{response.text[:200]}...'")

                # AUTO-HEAL (Auto-Heal Interceptor)
                # If the AI mistakenly outputted a technical call as plain text, we intercept it, convert it to a native FunctionCall, and run it!
                if response.text:
                    import ast
                    import time
                    
                    healed_calls = []
                    
                    # 1. Look for and parse JSON structures in the text
                    json_blocks = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response.text)
                    if not json_blocks:
                        # Fallback to balanced braces heuristic to find any JSON dictionary in the plain text
                        bracket_count = 0
                        start_idx = -1
                        for idx, char in enumerate(response.text):
                            if char == '{':
                                if bracket_count == 0:
                                    start_idx = idx
                                bracket_count += 1
                            elif char == '}':
                                bracket_count -= 1
                                if bracket_count == 0 and start_idx != -1:
                                    candidate = response.text[start_idx:idx+1]
                                    try:
                                        parsed = json.loads(candidate)
                                        if isinstance(parsed, dict):
                                            json_blocks.append(candidate)
                                    except Exception:
                                        pass
                                    start_idx = -1

                    for block_str in json_blocks:
                        try:
                            data = json.loads(block_str)
                            if not isinstance(data, dict):
                                continue
                                
                            # Case A: {"action": "execute_telegram_action", "method_name": "...", "parameters": {...}}
                            if "action" in data and data["action"] == "execute_telegram_action":
                                method_name = data.get("method_name")
                                params = data.get("parameters") or data.get("args") or {}
                                healed_calls.append({
                                    "name": "execute_telegram_action",
                                    "args": {
                                        "method_name": method_name,
                                        "args_json": json.dumps(params, ensure_ascii=False)
                                    }
                                })
                            # Case B: ReAct / LangChain style {"action": "tool_name", "action_input" / "prompt" / etc.}
                            elif "action" in data and data["action"] != "execute_telegram_action":
                                fn_name = data["action"]
                                active_tools = [t.name for t in registry.get_all_tools()]
                                if fn_name in active_tools:
                                    # If parameters are packed inside a wrapper key
                                    if "action_input" in data or "args" in data or "parameters" in data:
                                        action_input = data.get("action_input") or data.get("args") or data.get("parameters") or {}
                                        args = {}
                                        if isinstance(action_input, dict):
                                            args = action_input
                                        elif isinstance(action_input, str):
                                            # First pass: try parsing JSON or Python literal string
                                            try:
                                                args = json.loads(action_input)
                                            except Exception:
                                                try:
                                                    args = ast.literal_eval(action_input)
                                                except Exception:
                                                    args = action_input

                                            # Recursive unpack: resolve double-escaped or nested stringified JSONs
                                            while isinstance(args, str):
                                                try:
                                                    parsed_args = json.loads(args)
                                                    if isinstance(parsed_args, (dict, list)):
                                                        args = parsed_args
                                                        break
                                                except Exception:
                                                    pass
                                                try:
                                                    parsed_args = ast.literal_eval(args)
                                                    if isinstance(parsed_args, (dict, list)):
                                                        args = parsed_args
                                                        break
                                                except Exception:
                                                    pass
                                                break  # Stop to prevent infinite loops if parsing fails

                                            # If still a string after all attempts, perform dynamic signature mapping
                                            if isinstance(args, str):
                                                tool_meta = registry.get(fn_name)
                                                if tool_meta:
                                                    sig = inspect.signature(tool_meta.callable)
                                                    # Exclude self and generic varargs from parameter matching
                                                    param_names = [
                                                        p.name for p in sig.parameters.values() 
                                                        if p.name not in ['self', 'kwargs', 'args']
                                                    ]
                                                    if param_names:
                                                        args = {param_names[0]: args}
                                                    else:
                                                        args = {"text": args}
                                                else:
                                                    args = {"text": args}
                                    else:
                                        # Directly collect other keys as flat parameters (e.g. {"action": "generate_image", "prompt": "..."})
                                        args = {k: v for k, v in data.items() if k not in ["action", "parameters_schema"]}
                                        
                                    healed_calls.append({
                                        "name": fn_name,
                                        "args": args
                                    })
                            # Case C: {"name": "...", "args": {...}} or {"name": "...", "parameters": {...}}
                            elif "name" in data and ("args" in data or "parameters" in data or "arguments" in data):
                                fn_name = data["name"]
                                args = data.get("args") or data.get("parameters") or data.get("arguments") or {}
                                healed_calls.append({
                                    "name": fn_name,
                                    "args": args
                                })
                            # Case D: {"function": "...", "parameters": {...}}
                            elif "function" in data and ("parameters" in data or "args" in data or "arguments" in data):
                                fn_name = data["function"]
                                args = data.get("parameters") or data.get("args") or data.get("arguments") or {}
                                healed_calls.append({
                                    "name": fn_name,
                                    "args": args
                                })
                            # Case E: Native-like tool_calls list {"tool_calls": [{"name": "...", "arguments": {...}}]}
                            elif "tool_calls" in data and isinstance(data["tool_calls"], list):
                                for tc in data["tool_calls"]:
                                    if isinstance(tc, dict) and "name" in tc:
                                        fn_name = tc["name"]
                                        args = tc.get("arguments") or tc.get("args") or {}
                                        
                                        # If arguments is a string (escaped JSON/literal), recursively parse it
                                        if isinstance(args, str):
                                            try:
                                                args = json.loads(args)
                                            except Exception:
                                                try:
                                                    args = ast.literal_eval(args)
                                                except Exception:
                                                    pass
                                                    
                                        healed_calls.append({
                                            "name": fn_name,
                                            "args": args
                                        })
                            # Case F: Direct tool call JSON like {"generate_image": {"prompt": "..."}} (Uses FunctionRegistry dynamically)
                            else:
                                active_tools = [t.name for t in registry.get_all_tools()]
                                for key, val in data.items():
                                    if key in active_tools and isinstance(val, dict):
                                        healed_calls.append({
                                            "name": key,
                                            "args": val
                                        })
                        except Exception as json_err:
                            logger.debug(f"Auto-Heal failed to parse JSON block: {str(json_err)}")

                    # 2. Fallback to regular expression for Python-style calls like `tool_name(args)` if no JSON calls were found
                    if not healed_calls:
                        tool_matches = self.tool_pattern.findall(response.text)
                        for fn_name, args_str in tool_matches:
                            # Safely parse arguments via Abstract Syntax Tree (AST)
                            kwargs = {}
                            try:
                                tree = ast.parse(f"f({args_str})")
                                for kw in tree.body[0].value.keywords:
                                    kwargs[kw.arg] = ast.literal_eval(kw.value)
                            except Exception as ast_err:
                                logger.warning(f"Parsing via AST failed: {str(ast_err)}. Starting regular parser...")
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
                            healed_calls.append({
                                "name": fn_name,
                                "args": kwargs
                            })

                    # 3. Append healed calls to response parts dynamically
                    if healed_calls:
                        if response.candidates and response.candidates[0].content:
                            content_obj = response.candidates[0].content
                            if content_obj.parts is None:
                                content_obj.parts = []
                            
                            # Filter out text parts so raw JSON/code is not sent to the chat
                            content_obj.parts = [p for p in content_obj.parts if not p.text]
                            
                            for call in healed_calls:
                                fn_name = call["name"]
                                args = call["args"]
                                
                                healed_part = types.Part(
                                    function_call=types.FunctionCall(
                                        id=f"heal_{fn_name[:4]}_{int(time.time())}",
                                        name=fn_name,
                                        args=args,
                                        thought_signature=b"healed" # Bypasses thought_signature API validation
                                    )
                                )
                                content_obj.parts.append(healed_part)
                                
                                if fn_name == "no_op_ignore":
                                    should_ignore = True

                # Sending the reply to the current Chat as a reply strictly to the locked message from the start
                if response.text and not response.function_calls and not should_ignore:
                    typing_task.cancel()
                    try:
                        # Send the message and capture the result object containing the message ID
                        result = await self.client.send_message(chat_entity, response.text, reply_to=reply_to_id)
                        logger.info(f"Sent plain-text response to chat {chat_id}: '{response.text[:150]}...'")
                        
                        # Synchronously write the outgoing message to the DB immediately to eliminate the race condition
                        await self.db.save_message(str(chat_id), "model", response.text, msg_id=result.id)
                        
                        # Add to the global duplicate cache so bot.py ignores the incoming network event for this message
                        import bot
                        bot.processed_msg_ids.add((int(chat_id), result.id))
                    except Exception as tg_err:
                        logger.warning(f"Failed to deliver plain-text response to chat {chat_id}: {str(tg_err)}")
                        # Write the failure reason back to the DB to make the AI aware of the Telegram restriction
                        await self.db.save_message(
                            chat_id,
                            "user",
                            f"[System notification: Your last plain-text response failed to deliver due to Telegram error: {str(tg_err)}]"
                        )

                # Tool calls
                if response.function_calls:
                    logger.info(f"Received {len(response.function_calls)} tool call(s) from Gemini (Turn {turn + 1})")
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
                                logger.info(f"Tool call '{fn_name}' arguments: {args}")
                                logger.info(f"Tool call '{fn_name}' from registry...")
                                if inspect.iscoroutinefunction(tool_meta.callable):
                                    result = await tool_meta.callable(**args)
                                else:
                                    result = tool_meta.callable(**args)
                                    
                                logger.info(f"Tool '{fn_name}' execution completed. Result: '{str(result)[:500]}...'")
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
