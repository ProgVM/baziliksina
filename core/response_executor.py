# core/response_executor.py
import os
import sys
import json
import asyncio
import logging
import inspect
import re
from google.genai import types

import config
from registry import registry
from utils import safe_telegram_html
import tools

logger = logging.getLogger("ResponseExecutor")


class AIResponseExecutor:
    def __init__(self, telegram_client, db_manager):
        self.client = telegram_client
        self.db = db_manager

    async def execute_response(self, text: str, chat_entity, reply_to_id: int, chat_id: str) -> bool:
        """
        Parses sequentially/parallel/background blocks and executes contained segments chronologically.
        Returns should_ignore (bool) to signal if transaction should be closed immediately.
        """
        should_ignore = False
        cleaned_text = text

        # 1. Clean technical cross-chat prefixes [Chat: ... | Message ID: ...] and leaked thoughts headers
        prefix_pattern = re.compile(r'\[Chat:\s*-?\d+\s*\|\s*Message ID:\s*(?:\d+|unknown)\]\s*\n?', re.IGNORECASE)
        cleaned_text = prefix_pattern.sub("", cleaned_text).strip()
        
        thought_pattern = re.compile(r'^(?:thought|thinking|thoughts)(?:\s*:\s*|\s*\n+)?', re.IGNORECASE)
        cleaned_text = thought_pattern.sub("", cleaned_text).strip()

        # 2. Split response into block containers using config regex
        block_pattern = re.compile(config.RE_SEQ_BLOCK, re.DOTALL | re.IGNORECASE)
        
        blocks = []
        last_idx = 0
        
        for block_match in block_pattern.finditer(cleaned_text):
            start_pos, end_pos = block_match.span()
            before_part = cleaned_text[last_idx:start_pos].strip()
            if before_part:
                blocks.append(("seq", before_part))
            
            b_type = block_match.group(1).lower()
            b_content = block_match.group(2).strip()
            blocks.append((b_type, b_content))
            last_idx = end_pos
            
        after_part = cleaned_text[last_idx:].strip()
        if after_part:
            blocks.append(("seq", after_part))

        # 3. Process each sequential, parallel or background block
        for b_type, b_content in blocks:
            all_matches = []
            
            # Compiled regexes from configuration matrix
            tag_regexes_compiled = [
                (re.compile(config.RE_REPLY_TAG, re.IGNORECASE), "reply"),
                (re.compile(config.RE_REACT_TAG, re.IGNORECASE), "react"),
                (re.compile(config.RE_ATTACH_TAG, re.IGNORECASE), "attach"),
                (re.compile(config.RE_EDIT_TAG, re.IGNORECASE), "edit"),
                (re.compile(config.RE_DELETE_TAG, re.IGNORECASE), "delete"),
                (re.compile(config.RE_NOOP_TAG, re.IGNORECASE), "noop"),
                (re.compile(config.RE_TOOL_TAG, re.IGNORECASE), "tool")
            ]
            
            for regex, name in tag_regexes_compiled:
                for match in regex.finditer(b_content):
                    if name == "reply":
                        data = {"msg_id": int(match.group(1))}
                    elif name == "react":
                        data = {"msg_id": int(match.group(1)), "emoji": match.group(2)}
                    elif name == "attach":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "edit":
                        data = {"msg_id": int(match.group(1)), "text": match.group(2)}
                    elif name == "delete":
                        data = {"msg_id": int(match.group(1))}
                    elif name == "noop":
                        data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "tool":
                        data = {"tool_name": match.group(1), "args_str": match.group(2)}
                    all_matches.append((match.start(), match.end(), name, data))
                    
            xml_regexes_compiled = [
                (re.compile(r'<reply\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</reply>', re.IGNORECASE | re.DOTALL), "reply_msg"),
                (re.compile(r'<react\s+(?:msg_)?id=["\'](\d+)["\']\s+emoji=["\']([^"\']*)["\']\s*/?>', re.IGNORECASE), "react"),
                (re.compile(r'<attach\s+files=["\']([^"\']*)["\'](?:\s+caption=["\']([^"\']*)["\'])?\s*/?>', re.IGNORECASE), "attach"),
                (re.compile(r'<attach\s+files=["\']([^"\']*)["\']>(.*?)</attach>', re.IGNORECASE | re.DOTALL), "attach_tag"),
                (re.compile(r'<edit\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</edit>', re.IGNORECASE | re.DOTALL), "edit"),
                (re.compile(r'<delete\s+(?:msg_)?id=["\'](\d+)["\']\s*/?>', re.IGNORECASE), "delete"),
                (re.compile(r'<noop\s+reason=["\']([^"\']*)["\'](?:\s+continue=["\'](true|false)["\'])?\s*/?>', re.IGNORECASE), "noop"),
                (re.compile(r'<tool\s+name=["\']([a-zA-Z0-9_]+)["\']\s*([^>]*)\s*/?>', re.IGNORECASE), "tool")
            ]
            
            for regex, name in xml_regexes_compiled:
                for match in regex.finditer(b_content):
                    if name == "reply_msg":
                        data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "react":
                        data = {"msg_id": int(match.group(1)), "emoji": match.group(2)}
                    elif name == "attach":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "attach_tag":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2).strip()}
                        name = "attach"
                    elif name == "edit":
                        data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "delete":
                        data = {"msg_id": int(match.group(1))}
                    elif name == "noop":
                        data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "tool":
                        data = {"tool_name": match.group(1), "args_str": match.group(2)}
                    all_matches.append((match.start(), match.end(), name, data))

            # Chronologically sort all matched bracket actions and XML tags
            all_matches.sort(key=lambda x: x[0])
            
            segments = []
            last_seg_idx = 0
            
            for start, end, name, data in all_matches:
                before_seg_text = b_content[last_seg_idx:start].strip()
                if before_seg_text:
                    segments.append(("text", before_seg_text))
                segments.append((name, data))
                last_seg_idx = end
            
            after_seg_text = b_content[last_seg_idx:].strip()
            if after_seg_text:
                segments.append(("text", after_seg_text))
                
            # Merge text segments into preceding reply segments (bracket compatibility)
            merged_segments = []
            current_rep_id = None
            for s_type, s_data in segments:
                if s_type == "reply":
                    current_rep_id = s_data["msg_id"]
                elif s_type == "text":
                    if current_rep_id is not None:
                        merged_segments.append(("reply_msg", {"msg_id": current_rep_id, "text": s_data}))
                        current_rep_id = None
                    else:
                        merged_segments.append(("msg", {"text": s_data}))
                elif s_type == "reply_msg":
                    merged_segments.append(("reply_msg", s_data))
                else:
                    merged_segments.append((s_type, s_data))
            if current_rep_id is not None:
                merged_segments.append(("reply_msg", {"msg_id": current_rep_id, "text": ""}))

            async def execute_segment(segment):
                nonlocal should_ignore
                s_type, s_data = segment
                
                if s_type == "msg":
                    raw_text = s_data["text"]
                    unescaped_text = raw_text.replace(r'\[', '[').replace(r'\]', ']')
                    formatted_html = safe_telegram_html(unescaped_text)
                    try:
                        result = await self.client.send_message(chat_entity, formatted_html, reply_to=reply_to_id, parse_mode="html")
                        await self.db.save_message(str(chat_id), "model", unescaped_text, msg_id=result.id)
                        import bot
                        bot.processed_msg_ids.add((int(chat_id), result.id))
                    except Exception as tg_err:
                        logger.error(f"Failed to deliver msg: {str(tg_err)}")
                    
                elif s_type == "reply_msg":
                    raw_text = s_data["text"]
                    unescaped_text = raw_text.replace(r'\[', '[').replace(r'\]', ']')
                    formatted_html = safe_telegram_html(unescaped_text)
                    try:
                        result = await self.client.send_message(chat_entity, formatted_html, reply_to=int(s_data["msg_id"]), parse_mode="html")
                        await self.db.save_message(str(chat_id), "model", unescaped_text, msg_id=result.id)
                        import bot
                        bot.processed_msg_ids.add((int(chat_id), result.id))
                    except Exception as tg_err:
                        logger.error(f"Failed to deliver reply msg: {str(tg_err)}")
                    
                elif s_type == "react":
                    emoji = s_data["emoji"]
                    is_add = emoji.lower() != "none"
                    await tools.toolkit.set_message_reaction(chat_entity, s_data["msg_id"], reaction_emoji=emoji if is_add else None, is_add=is_add)
                    
                elif s_type == "attach":
                    await tools.toolkit.send_media_message(chat_id=chat_entity, files=s_data["files"], caption=s_data["caption"])
                    
                elif s_type == "edit":
                    await tools.toolkit.edit_message(chat_entity, s_data["msg_id"], s_data["text"])
                    
                elif s_type == "delete":
                    await tools.toolkit.delete_message(chat_entity, s_data["msg_id"])
                    
                elif s_type == "noop":
                    tools.toolkit.no_op_ignore(s_data["reason"], continue_loop=s_data["continue"])
                    if not s_data["continue"]:
                        should_ignore = True
                        
                elif s_type == "tool":
                    t_name = s_data["tool_name"]
                    t_args_str = s_data["args_str"]
                    t_args = {}
                    try:
                        t_args = json.loads(t_args_str)
                    except Exception:
                        pairs = re.findall(r'(\w+)=["\']([^"\']*)["\']', t_args_str)
                        if pairs:
                            t_args = {k: v for k, v in pairs}
                        else:
                            import ast
                            try:
                                tree = ast.parse(f"f({t_args_str})")
                                for kw in tree.body[0].value.keywords:
                                    t_args[kw.arg] = ast.literal_eval(kw.value)
                            except Exception:
                                t_args = {"query": t_args_str, "text": t_args_str}
                            
                    tool_meta = registry.get(t_name)
                    if tool_meta:
                        try:
                            if inspect.iscoroutinefunction(tool_meta.callable):
                                tool_res = await tool_meta.callable(**t_args)
                            else:
                                tool_res = tool_meta.callable(**t_args)
                            await self.db.save_message(str(chat_id), "user", f"[System: Tool '{t_name}' executed. Result: {tool_res}]")
                        except Exception as terr:
                            logger.error(f"Error executing tool label {t_name}: {str(terr)}")

            # Block Execution Scheduling (Sequential / Parallel / Background tasks)
            if b_type == "seq":
                for segment in merged_segments:
                    await execute_segment(segment)
            elif b_type == "par":
                tasks_list = [execute_segment(segment) for segment in merged_segments]
                await asyncio.gather(*tasks_list, return_exceptions=True)
            elif b_type == "bg":
                for segment in merged_segments:
                    asyncio.create_task(execute_segment(segment))

        return should_ignore
