# core/gemini_manager.py
import json
import os
import asyncio
import logging
import hashlib
import inspect
import re
import time
import config
from google.genai import types
from google.genai.errors import APIError

from key_manager import GeminiKeyManager, PollinationsKeyManager
from db_manager import DBManager
from registry import registry
from prompt_interpolator import get_interpolated_prompt
from context_manager import AIContextManager
from response_executor import AIResponseExecutor
import tools

logger = logging.getLogger("GeminiManager")


class GeminiManager:
    def __init__(self, telegram_client, db_manager):
        self.client = telegram_client
        self.db = db_manager
        self.key_manager = GeminiKeyManager(db_manager)
        self.pollinations_key_manager = PollinationsKeyManager(db_manager)
        self.context_mgr = AIContextManager(db_manager, self.key_manager)
        self.executor = AIResponseExecutor(telegram_client, db_manager)
        self._last_system_prompt_hash = None

    @property
    def tool_pattern(self):
        """Dynamically constructs a regular expression containing all active tool names."""
        tool_names = [t.name for t in registry.get_all_tools()]
        if not tool_names:
            return re.compile(r"(?!)")
        return re.compile(
            r"(?:tools\.)?(" + "|".join(re.escape(name) for name in tool_names) + r")\s*\((.*?)\)",
            re.DOTALL | re.IGNORECASE
        )

    async def handle_query(self, chat_id: str, chat_entity=None, trigger_msg_id: int = None):
        """Orchestrates dialogue turns, token limits, and segment actions across decoupled sub-modules."""
        reply_to_id = trigger_msg_id
        
        if not reply_to_id:
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

        # Configure dynamic context variables
        tools.current_chat_id.set(int(chat_id))
        tools.current_reply_to_id.set(reply_to_id)
        
        # Load system instructions dynamically via PromptInterpolator
        system_prompt = await get_interpolated_prompt(self.client, config.CHARACTER_FILE, use_system_prompt=config.USE_SYSTEM_PROMPT)
        logger.info(f"Loaded system prompt from disk: {len(system_prompt)} characters.")

        try:
            chat_title = getattr(chat_entity, "title", None) or "Private Chat"
            chat_username = getattr(chat_entity, "username", None) or "no"
        except Exception:
            chat_title, chat_username = "Chat", "no"

        # Load environment context dynamically from env_prompt.txt
        env_path = config.BASE_DIR / "config" / "env_prompt.txt"
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    env_template = f.read().strip()
            except Exception as e:
                logger.error(f"Error reading env_prompt.txt: {str(e)}")
                env_template = "You are in chat {chat_id}."
        else:
            env_template = "You are in chat {chat_id}."
            
        is_admin = False
        admin_details = []
        custom_title_status = "None"
        if chat_entity and not isinstance(chat_entity, (int, str)):
            is_group = getattr(chat_entity, "megagroup", False) or getattr(chat_entity, "broadcast", False) or type(chat_entity).__name__ == "Chat"
            if is_group:
                try:
                    participant = await self.client.get_permissions(chat_entity, "me")
                    is_admin = getattr(participant, "is_admin", False) or getattr(participant, "is_creator", False)
                    from telethon.tl.functions.channels import GetParticipantRequest
                    res = await self.client(GetParticipantRequest(channel=chat_entity, participant="me"))
                    raw_participant = res.participant
                    custom_title = getattr(raw_participant, "rank", None)
                    if custom_title:
                        custom_title_status = f"'{custom_title}'"
                    if is_admin:
                        admin_details.append("Admin/Owner (You have administrative powers!)")
                        rights = []
                        if getattr(participant, "delete_messages", False): rights.append("delete messages")
                        if getattr(participant, "ban_users", False): rights.append("ban/restrict users")
                        if getattr(participant, "pin_messages", False): rights.append("pin messages")
                        if getattr(participant, "invite_users", False): rights.append("invite users")
                        if getattr(participant, "change_info", False): rights.append("change group info")
                        if getattr(participant, "anonymous", False): rights.append("post anonymously")
                        if rights:
                            admin_details.append(f"  * Your permissions: can {', '.join(rights)}.")
                    else:
                        admin_details.append("Regular member (No administrative powers)")
                except Exception as e:
                    logger.debug(f"Failed to check admin rights: {str(e)}")
        admin_status = "\n".join(admin_details) if admin_details else "Regular member / Private Chat partner"

        env_prompt = env_template.replace("{chat_id}", str(chat_id)).replace("{chat_title}", chat_title).replace("{chat_username}", chat_username)
        env_prompt = f"{env_prompt}\nYour administrative privileges in this chat: {admin_status}\nYour custom Member Tag / Custom Title in this chat: {custom_title_status}"
        dynamic_prompt = f"{system_prompt}\n\n{env_prompt}"
        logger.info(f"Full dynamic system_instruction passed to Gemini: {len(dynamic_prompt)} characters.")
        if not chat_entity or isinstance(chat_entity, (int, str)):
            chat_entity = tools.entity_cache.get(int(chat_id))

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
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=get_safety_threshold(config.SAFETY_HATE_SPEECH)),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=get_safety_threshold(config.SAFETY_HARASSMENT)),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=get_safety_threshold(config.SAFETY_SEXUALLY_EXPLICIT)),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=get_safety_threshold(config.SAFETY_DANGEROUS_CONTENT)),
        ]

        # Filter allowed tools based on config matrix
        allowed_callables = []
        for tool in registry.get_all_tools():
            is_blocked = False
            if not tool.is_custom:
                if config.AI_BLOCKED_ROOT_TOOLS and tool.name in config.AI_BLOCKED_ROOT_TOOLS:
                    is_blocked = True
                if config.AI_ALLOWED_ROOT_TOOLS and "all" not in config.AI_ALLOWED_ROOT_TOOLS and tool.name not in config.AI_ALLOWED_ROOT_TOOLS:
                    is_blocked = True
            else:
                if config.AI_BLOCKED_CUSTOM_TOOLS and tool.name in config.AI_BLOCKED_CUSTOM_TOOLS:
                    is_blocked = True
                if config.AI_ALLOWED_CUSTOM_TOOLS and "all" not in config.AI_ALLOWED_CUSTOM_TOOLS and tool.name not in config.AI_ALLOWED_CUSTOM_TOOLS:
                    is_blocked = True
            if not is_blocked:
                func = tool.callable
                try:
                    # Map arbitrary keyword argument expansions into an explicit dict parameter
                    sig = inspect.signature(func)
                    clean_params = []
                    has_kwargs = False
                    has_args = False
                    for p in sig.parameters.values():
                        if p.kind == inspect.Parameter.VAR_KEYWORD:
                            has_kwargs = True
                        elif p.kind == inspect.Parameter.VAR_POSITIONAL:
                            has_args = True
                        elif p.kind != inspect.Parameter.VAR_POSITIONAL:
                            clean_params.append(p)
                    if has_args:
                        args_param = inspect.Parameter(
                            name="args",
                            kind=inspect.Parameter.KEYWORD_ONLY,
                            default=None,
                            annotation=list
                        )
                        clean_params.append(args_param)
                    if has_kwargs:
                        kwargs_param = inspect.Parameter(
                            name="kwargs",
                            kind=inspect.Parameter.KEYWORD_ONLY,
                            default=None,
                            annotation=dict
                        )
                        clean_params.append(kwargs_param)
                    func.__signature__ = sig.replace(parameters=clean_params)
                except Exception:
                    pass
                allowed_callables.append(func)

        config_obj = types.GenerateContentConfig(
            system_instruction=dynamic_prompt if config.USE_SYSTEM_PROMPT else None,
            tools=allowed_callables,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            safety_settings=safety_settings,
            temperature=config.TEMPERATURE,
            top_p=config.TOP_P,
            stop_sequences=config.STOP_SEQUENCES if config.STOP_SEQUENCES else None,
            max_output_tokens=self.key_manager.output_token_limit,
        )

        async def send_typing_loop():
            try:
                async with self.client.action(chat_entity, 'typing'):
                    while True:
                        await asyncio.sleep(config.TYPING_INTERVAL)
            except (asyncio.CancelledError, Exception):
                pass

        typing_task = asyncio.create_task(send_typing_loop())

        max_turns = config.MAX_TURNS
        should_ignore = False

        try:
            for turn in range(max_turns):
                # 1. Load aligned history context chronologically via ContextManager
                contents = await self.context_mgr.get_aligned_history(chat_id, gemini_client)

                # Inject dynamic custom title and admin rights directly into the active chat header
                for content in contents:
                    if content.parts and content.parts[0].text and "[System notification: Active conversation thread" in content.parts[0].text:
                        reply_to_id_str = str(reply_to_id) if reply_to_id else "unknown"
                        content.parts[0].text = (
                            f"{content.parts[0].text}\n"
                            f"- Your custom Member Tag in this active chat: {custom_title_status}\n"
                            f"- Your administrative privileges: {admin_status}\n"
                            f"- Current active triggering Message ID: {reply_to_id_str} (All your plain conversational replies and default <reply> tags MUST target this Message ID unless you explicitly reply to another ID)"
                        )
                        break

                # 2. High-precision token counting and context-limit checks
                try:
                    token_response = await gemini_client.aio.models.count_tokens(
                        model=self.key_manager.get_model(),
                        contents=contents
                    )
                    total_tokens = token_response.total_tokens
                    logger.info(f"Chat context {chat_id}: {total_tokens} tokens.")
                    
                    if total_tokens > self.key_manager.input_token_limit:
                        await self.context_mgr.summarize_chat_context(gemini_client)
                        continue
                except APIError as e:
                    if e.code == 403 and ("permission" in str(e).lower() or "exist" in str(e).lower() or "access" in str(e).lower()):
                        logger.warning("Gemini API 403 error caught during token counting. Healing context...")
                        file_match = re.search(r"File\s+([a-zA-Z0-9_-]+)", str(e), re.IGNORECASE)
                        if not file_match:
                            file_match = re.search(r"files/([a-zA-Z0-9_-]+)", str(e), re.IGNORECASE)
                        
                        if file_match:
                            file_id = file_match.group(1)
                            await self.context_mgr._heal_inaccessible_file(file_id, contents)
                            await asyncio.sleep(config.TIMEOUT_SLEEP)
                            continue
                    logger.error(f"Error counting tokens: {str(e)}")
                except Exception as count_err:
                    logger.error(f"Error counting tokens: {str(count_err)}")

                logger.info(f"Requesting generation from Gemini API (Turn {turn + 1}/{max_turns})...")
                try:
                    response = await asyncio.wait_for(
                        gemini_client.aio.models.generate_content(
                            model=self.key_manager.get_model(),
                            contents=contents,
                            config=config_obj
                        ),
                        timeout=GEMINI_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("Model response timeout. Retrying...")
                    await asyncio.sleep(TIMEOUT_SLEEP)
                    continue
                except APIError as e:
                    if e.code == 429:
                        active_key = self.key_manager.keys[self.key_manager.current_key_index]
                        logger.warning(f"Gemini API 429 encountered on key: '{active_key[:10]}...'. Rotating pools...")
                        await asyncio.sleep(config.RATE_LIMIT_SLEEP)
                        await self.key_manager.handle_error_exhausted(str(e))
                        gemini_client = await self.key_manager.rotate_key_async(str(e))
                        continue
                    elif e.code == 401 or "unauthenticated" in str(e).lower() or "credentials" in str(e).lower():
                        active_key = self.key_manager.keys[self.key_manager.current_key_index]
                        logger.error(f"Gemini API 401 Invalid Credentials on key: '{active_key[:10]}...'. Rotating pools...")
                        await self.key_manager.mark_key_exhausted(str(e))
                        gemini_client = await self.key_manager.rotate_key_async(str(e))
                        continue
                    elif e.code == 403 and ("permission" in str(e).lower() or "exist" in str(e).lower() or "access" in str(e).lower()):
                        logger.warning("Gemini API 403 error caught during generation. Healing context...")
                        file_match = re.search(r"File\s+([a-zA-Z0-9_-]+)", str(e), re.IGNORECASE)
                        if not file_match:
                            file_match = re.search(r"files/([a-zA-Z0-9_-]+)", str(e), re.IGNORECASE)
                        
                        if file_match:
                            file_id = file_match.group(1)
                            await self.context_mgr._heal_inaccessible_file(file_id, contents)
                            await asyncio.sleep(TIMEOUT_SLEEP)
                            continue
                        raise e
                    elif e.code in [502, 503, 504]:
                        logger.warning(f"Gemini API transient error {e.code} received. Retrying in {config.API_ERROR_SLEEP}s...")
                        await asyncio.sleep(config.API_ERROR_SLEEP)
                        continue
                    else:
                        raise e

                # 3. Extract function calls
                function_calls_to_execute = []
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if part.function_call:
                            function_calls_to_execute.append(part.function_call)

                # 4. Auto-Heal Interceptor (Resolves plain-text output leaks)
                try:
                    resp_text = response.text
                except Exception:
                    resp_text = None

                if resp_text:
                    import ast
                    import time
                    healed_calls = []
                    
                    json_blocks = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', resp_text)
                    if not json_blocks:
                        bracket_count = 0
                        start_idx = -1
                        for idx, char in enumerate(resp_text):
                            if char == '{':
                                if bracket_count == 0:
                                    start_idx = idx
                                bracket_count += 1
                            elif char == '}':
                                bracket_count -= 1
                                if bracket_count == 0 and start_idx != -1:
                                    candidate = resp_text[start_idx:idx+1]
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
                                
                            if "action" in data and data["action"] == "execute_telegram_action":
                                method_name = data.get("method_name")
                                params = data.get("parameters") or data.get("args") or {}
                                healed_calls.append({
                                    "name": "execute_telegram_action",
                                    "args": {"method_name": method_name, "args_json": json.dumps(params, ensure_ascii=False)}
                                })
                            elif "action" in data and data["action"] != "execute_telegram_action":
                                fn_name = data["action"]
                                active_tools = [t.name for t in registry.get_all_tools()]
                                if fn_name in active_tools:
                                    if "action_input" in data or "args" in data or "parameters" in data:
                                        action_input = data.get("action_input") or data.get("args") or data.get("parameters") or {}
                                        args = {}
                                        if isinstance(action_input, dict):
                                            args = action_input
                                        elif isinstance(action_input, str):
                                            try: args = json.loads(action_input)
                                            except Exception:
                                                try: args = ast.literal_eval(action_input)
                                                except Exception: args = action_input

                                            while isinstance(args, str):
                                                try:
                                                    parsed_args = json.loads(args)
                                                    if isinstance(parsed_args, (dict, list)):
                                                        args = parsed_args
                                                        break
                                                except Exception: pass
                                                try:
                                                    parsed_args = ast.literal_eval(args)
                                                    if isinstance(parsed_args, (dict, list)):
                                                        args = parsed_args
                                                        break
                                                except Exception: pass
                                                break

                                            if isinstance(args, str):
                                                tool_meta = registry.get(fn_name)
                                                if tool_meta:
                                                    sig = inspect.signature(tool_meta.callable)
                                                    param_names = [p.name for p in sig.parameters.values() if p.name not in ['self', 'kwargs', 'args']]
                                                    if param_names: args = {param_names[0]: args}
                                                    else: args = {"text": args}
                                                else: args = {"text": args}
                                    else:
                                        args = {k: v for k, v in data.items() if k not in ["action", "parameters_schema"]}
                                    healed_calls.append({"name": fn_name, "args": args})
                            elif "name" in data and ("args" in data or "parameters" in data or "arguments" in data):
                                healed_calls.append({"name": data["name"], "args": data.get("args") or data.get("parameters") or data.get("arguments") or {}})
                            elif "function" in data and ("parameters" in data or "args" in data or "arguments" in data):
                                healed_calls.append({"name": data["function"], "args": data.get("parameters") or data.get("args") or data.get("arguments") or {}})
                            elif "tool_calls" in data and isinstance(data["tool_calls"], list):
                                for tc in data["tool_calls"]:
                                    if isinstance(tc, dict) and "name" in tc:
                                        fn_name = tc["name"]
                                        args = tc.get("arguments") or tc.get("args") or {}
                                        if isinstance(args, str):
                                            try: args = json.loads(args)
                                            except Exception:
                                                try: args = ast.literal_eval(args)
                                                except Exception: pass
                                        healed_calls.append({"name": fn_name, "args": args})
                            else:
                                active_tools = [t.name for t in registry.get_all_tools()]
                                for key, val in data.items():
                                    if key in active_tools and isinstance(val, dict):
                                        healed_calls.append({"name": key, "args": val})
                        except Exception:
                            pass

                    if not healed_calls:
                        tool_matches = self.tool_pattern.findall(resp_text)
                        for fn_name, args_str in tool_matches:
                            kwargs_dict = {}
                            try:
                                tree = ast.parse(f"f({args_str})")
                                for kw in tree.body[0].value.keywords:
                                    kwargs_dict[kw.arg] = ast.literal_eval(kw.value)
                            except Exception:
                                pairs = re.findall(r"([a-zA-Z0-9_-]+)\s*=\s*(['\"].*?['\"]|\d+(?:\.\d+)?)", args_str)
                                for k, v in pairs:
                                    kwargs_dict[k] = v.strip("'\"")
                                    if kwargs_dict[k].isdigit():
                                        kwargs_dict[k] = int(kwargs_dict[k])
                                    else:
                                        try: kwargs_dict[k] = float(kwargs_dict[k])
                                        except ValueError: pass
                            healed_calls.append({"name": fn_name, "args": kwargs_dict})

                    if healed_calls:
                        if response.candidates and response.candidates[0].content:
                            content_obj = response.candidates[0].content
                            
                            # Извлекаем оригинальную подпись thought_signature перед модификацией частей
                            orig_thought_sig = None
                            if content_obj.parts:
                                for p in content_obj.parts:
                                    if hasattr(p, "thought_signature") and p.thought_signature:
                                        orig_thought_sig = p.thought_signature
                                        break
                                    elif hasattr(p, "thoughtSignature") and p.thoughtSignature:
                                        orig_thought_sig = p.thoughtSignature
                                        break

                            # Если оригинальная подпись отсутствует, используем официальный bypass-маркер Google
                            if not orig_thought_sig:
                                orig_thought_sig = b"skip_thought_signature_validator"

                            if content_obj.parts is None:
                                content_obj.parts = []
                            content_obj.parts = [p for p in content_obj.parts if not p.text]
                            
                            for call in healed_calls:
                                healed_part = types.Part(
                                    function_call=types.FunctionCall(
                                        id=f"heal_{call['name'][:4]}_{int(time.time())}",
                                        name=call["name"],
                                        args=call["args"]
                                    ),
                                    thought_signature=orig_thought_sig
                                )
                                content_obj.parts.append(healed_part)
                                if call["name"] == "no_op_ignore":
                                    should_ignore = True
                            
                            logger.info(f"Auto-Heal Interceptor: successfully healed {len(healed_calls)} call(s) from plain conversational text: {[c['name'] for c in healed_calls]}")
                            function_calls_to_execute = []
                            for part in response.candidates[0].content.parts:
                                if part.function_call:
                                    function_calls_to_execute.append(part.function_call)

                # 5. Hand over text response executing block-level tags dynamically to ResponseExecutor
                if resp_text and not function_calls_to_execute and not should_ignore:
                    typing_task.cancel()
                    should_ignore, should_continue = await self.executor.execute_response(resp_text, chat_entity, reply_to_id, chat_id)
                    if should_continue:
                        continue

                # 6. Process tool calls
                if function_calls_to_execute:
                    logger.info(f"Received {len(function_calls_to_execute)} tool call(s) from Gemini...")
                    model_tool_call_content = types.Content(role="model", parts=response.candidates[0].content.parts)
                    contents.append(model_tool_call_content)
                    await self.db.save_message(chat_id, "model", content_obj=model_tool_call_content)
                    
                    tool_responses = []
                    additional_parts = []
                    
                    for call in function_calls_to_execute:
                        fn_name = call.name
                        args = call.args
                        result = None
                        
                        tool_meta = registry.get(fn_name)
                        if tool_meta:
                            try:
                                logger.info(f"Tool call '{fn_name}' from registry...")
                                # Unpack dynamic list and dict arguments back to positional and keyword arguments
                                call_args = args.copy() if args else {}
                                extra_args = call_args.pop("args", []) or []
                                if "kwargs" in call_args and isinstance(call_args["kwargs"], dict):
                                    extra = call_args.pop("kwargs")
                                    call_args.update(extra)
                                    
                                if not isinstance(extra_args, list):
                                    extra_args = [extra_args]
                                    
                                # Safely bind arguments to resolve positional-or-keyword conflicts
                                sig = inspect.signature(tool_meta.callable)
                                positional_params = []
                                has_var_positional = False
                                for p in sig.parameters.values():
                                    if p.kind in [inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD]:
                                        positional_params.append(p.name)
                                    elif p.kind == inspect.Parameter.VAR_POSITIONAL:
                                        has_var_positional = True
                                        
                                pos_args = []
                                kw_args = {}
                                for name in positional_params:
                                    if name in call_args:
                                        pos_args.append(call_args[name])
                                if has_var_positional:
                                    pos_args.extend(extra_args)
                                    
                                for k, v in call_args.items():
                                    if k not in positional_params:
                                        kw_args[k] = v
                                        
                                if inspect.iscoroutinefunction(tool_meta.callable):
                                    result = await tool_meta.callable(*pos_args, **kw_args)
                                else:
                                    result = tool_meta.callable(*pos_args, **kw_args)
                                    
                                if fn_name == "upload_file_to_google" and isinstance(result, dict) and result.get("status") == "success":
                                    g_uri = result.get("google_uri")
                                    m_type = result.get("mime_type")
                                    if g_uri and m_type:
                                        additional_parts.append(types.Part.from_uri(file_uri=g_uri, mime_type=m_type))
                            except Exception as fn_err:
                                result = f"Error executing tool '{fn_name}': {str(fn_err)}"
                        else:
                            result = f"Error: Function '{fn_name}' is not registered."

                        tool_responses.append(types.Part.from_function_response(name=fn_name, response={"result": result}))
                        
                        # Universal extraction of Google File URIs from any tool result!
                        if result:
                            res_str = str(result)
                            GOOGLE_FILE_URI_REGEX = re.compile(
                                r"(https://generativelanguage\.googleapis\.com/(?:upload/)?v[0-9a-zA-Z_]+/files/[a-zA-Z0-9_-]+)",
                                re.IGNORECASE
                            )
                            uris = GOOGLE_FILE_URI_REGEX.findall(res_str)
                            for uri in uris:
                                try:
                                    m_type = await self.db.get_memory(uri)
                                    if m_type:
                                        logger.info(f"[Universal Tool Interceptor]: Detected Google URI in tool result: {uri}. Binding native Part.from_uri...")
                                        additional_parts.append(types.Part.from_uri(file_uri=uri, mime_type=m_type))
                                except Exception as uri_err:
                                    logger.error(f"Failed to bind universal tool part for {uri}: {str(uri_err)}")
                    
                    user_tool_resp_content = types.Content(role="user", parts=tool_responses)
                    contents.append(user_tool_resp_content)
                    await self.db.save_message(chat_id, "user", content_obj=user_tool_resp_content)
                    
                    if additional_parts:
                        user_media_content = types.Content(
                            role="user",
                            parts=[types.Part.from_text(text="[System notification: Visual files found by search or generation tools]")] + additional_parts
                        )
                        contents.append(user_media_content)
                        await self.db.save_message(chat_id, "user", content_obj=user_media_content)
                    
                    if should_ignore:
                        typing_task.cancel()
                        break
                    continue
                else:
                    break
                    
        except Exception as e:
            logger.error(f"Critical Gemini error in GeminiManager: {str(e)}")
        finally:
            typing_task.cancel()
            # Save the highest user message ID processed during this transaction
            try:
                async with self.db.db.execute(
                    "SELECT MAX(msg_id) FROM messages WHERE chat_id = ? AND role = 'user'", (str(chat_id),)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0] is not None:
                        tools.last_processed_user_msg_id[str(chat_id)] = int(row[0])
            except Exception as e_track:
                logger.error(f"Failed to update last processed user message ID: {str(e_track)}")


entity_cache = {}