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

METADATA_CLEAN_PATTERNS = [
    re.compile(r'(?<!\\)\[Chat:\s*-?\d+\s*\|\s*Message ID:\s*(?:\d+|unknown)(?:\s*\|\s*Date:[^\]]+)?\]\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Original\s+text\s+\([^)]+\):\s*.*?\]\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Reply\s+to\s+message\s+#?\d+(?:\s+in\s+[^\]]+)?\]\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Selected\s+fragment\s+/\s+Quote\]:\s*[\'"].*?[\'"]\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Selected\s+fragment\s+/\s+Quote\]:\s*[^\n]+\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Reactions\s+on\s+message\]:\s*[^\n]+\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[System\s+notification:\s*[^\]]+\]\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Inline\s+buttons\s+[^\]]+\]:\s*[^\n]+\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Reply\s+Keyboard\s+buttons\s+[^\]]+\]:\s*[^\n]+\s*\n?', re.IGNORECASE),
    re.compile(r'(?<!\\)\[Attached\s+Media\s+-\s*[^\]]+\]\s*\n?', re.IGNORECASE)
]


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
        for pattern in METADATA_CLEAN_PATTERNS:
            cleaned_text = pattern.sub("", cleaned_text)
        
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
                        parts = [p.strip() for p in match.group(2).split("|")]
                        if len(parts) > 1:
                            data = {"msg_id": int(match.group(1)), "emoji": parts[0], "action": parts[1]}
                        else:
                            data = {"msg_id": int(match.group(1)), "emoji": parts[0], "action": "set"}
                    elif name == "attach":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "edit":
                        data = {"msg_id": int(match.group(1)), "text": match.group(2)}
                    elif name == "delete":
                        data = {"msg_id": int(match.group(1))}
                    elif name == "pin":
                        data = {"msg_id": int(match.group(1)), "notify": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "unpin":
                        data = {"msg_id": int(match.group(1)) if match.group(1) else None}
                    elif name == "noop":
                        data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "tool":
                        data = {"tool_name": match.group(1), "args_str": match.group(2)}
                    all_matches.append((match.start(), match.end(), name, data))
                    
            xml_regexes_compiled = [
                (re.compile(r'(?<!\\)<reply\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</reply>', re.IGNORECASE | re.DOTALL), "reply_msg"),
                (re.compile(r'(?<!\\)<react\s+([^>]*)\s*/?>', re.IGNORECASE), "react_tag"),
                (re.compile(r'(?<!\\)<attach\s+files=["\']([^"\']*)["\'](?:\s+caption=["\']([^"\']*)["\'])?\s*/?>', re.IGNORECASE), "attach"),
                (re.compile(r'(?<!\\)<attach\s+files=["\']([^"\']*)["\']>(.*?)</attach>', re.IGNORECASE | re.DOTALL), "attach_tag"),
                (re.compile(r'(?<!\\)<edit\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</edit>', re.IGNORECASE | re.DOTALL), "edit"),
                (re.compile(r'(?<!\\)<delete\s+(?:msg_)?id=["\'](\d+)["\']\s*/?>', re.IGNORECASE), "delete"),
                (re.compile(r'(?<!\\)<pin\s+(?:msg_)?id=["\'](\d+)["\'](?:\s+notify=["\'](true|false)["\'])?\s*/?>', re.IGNORECASE), "pin"),
                (re.compile(r'(?<!\\)<unpin(?:\s+(?:msg_)?id=["\'](\d+)["\'])?\s*/?>', re.IGNORECASE), "unpin"),
                (re.compile(r'(?<!\\)<(?:noop|no_op_ignore)\s+reason=["\']([^"\']*)["\'](?:\s+continue=["\'](true|false)["\'])?\s*/?>', re.IGNORECASE), "noop"),
                (re.compile(r'(?<!\\)<tool\s+name=["\']([a-zA-Z0-9_]+)["\']\s*([^>]*)\s*/?>', re.IGNORECASE), "tool")
            ]
            
            for regex, name in xml_regexes_compiled:
                for match in regex.finditer(b_content):
                    if name == "reply_msg":
                        data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "react_tag":
                        attrs_str = match.group(1)
                        id_m = re.search(r'(?:msg_)?id=["\'](\d+)["\']', attrs_str, re.IGNORECASE)
                        emoji_m = re.search(r'emoji=["\']([^"\']*)["\']', attrs_str, re.IGNORECASE)
                        action_m = re.search(r'action=["\']([^"\']*)["\']', attrs_str, re.IGNORECASE)
                        data = {
                            "msg_id": int(id_m.group(1)) if id_m else None,
                            "emoji": emoji_m.group(1) if emoji_m else None,
                            "action": action_m.group(1) if action_m else "set"
                        }
                        name = "react"
                    elif name == "attach":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "attach_tag":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2).strip()}
                        name = "attach"
                    elif name == "edit": data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "delete": data = {"msg_id": int(match.group(1))}
                    elif name == "pin":
                        data = {"msg_id": int(match.group(1)), "notify": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "unpin":
                        data = {"msg_id": int(match.group(1)) if match.group(1) else None}
                    elif name == "noop": data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
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
                
                from utils import matches_filter
                from registry import tag_block_registry
                if not matches_filter(s_type, config.AI_TAG_WHITELIST, config.AI_TAG_BLACKLIST):
                    logger.warning(f"AI tag '{s_type}' is blocked by configuration. Skipping.")
                    return
                handler_meta = tag_block_registry.get(s_type)
                if handler_meta:
                    try:
                        await handler_meta.callable(s_data, chat_entity, reply_to_id, chat_id, self.client, self.db)
                        if s_type in ["noop", "no_op_ignore"] and not s_data.get("continue", False):
                            should_ignore = True
                    except Exception as err:
                        logger.error(f"Error executing tag '{s_type}': {str(err)}")
                else:
                    logger.warning(f"Tag handler '{s_type}' is not registered.")

            # Block Execution Scheduling (Sequential / Parallel / Background tasks)
            from utils import matches_filter
            if not matches_filter(b_type, config.AI_BLOCK_WHITELIST, config.AI_BLOCK_BLACKLIST):
                logger.warning(f"AI block '{b_type}' is blocked by configuration. Falling back to 'seq'.")
                b_type = "seq"
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

    async def parse_execute_and_strip_tags(self, text: str, chat_entity, reply_to_id: int, chat_id: str) -> str:
        """
        Parses nested sequential, parallel, and background blocks inside a string,
        """
        if not text:
            return text
        
        cleaned_text = text
        # 1. Clean technical prefixes and thought logs
        for pattern in METADATA_CLEAN_PATTERNS:
            cleaned_text = pattern.sub("", cleaned_text)
        
        thought_pattern = re.compile(r'^(?:thought|thinking|thoughts)(?:\s*:\s*|\s*\n+)?', re.IGNORECASE)
        cleaned_text = thought_pattern.sub("", cleaned_text).strip()

        # 2. Parse block containers inside the string
        block_pattern = re.compile(config.RE_SEQ_BLOCK, re.DOTALL | re.IGNORECASE)
        
        blocks = []
        last_idx = 0
        ranges_to_strip = []
        
        for block_match in block_pattern.finditer(cleaned_text):
            start_pos, end_pos = block_match.span()
            before_part = cleaned_text[last_idx:start_pos].strip()
            if before_part:
                blocks.append(("seq", before_part))
            
            b_type = block_match.group(1).lower()
            b_content = block_match.group(2).strip()
            blocks.append((b_type, b_content))
            
            ranges_to_strip.append((start_pos, end_pos))
            last_idx = end_pos
            
        after_part = cleaned_text[last_idx:].strip()
        if after_part:
            blocks.append(("seq", after_part))

        # Helper to execute a single block with sequential/parallel/background scheduling
        async def execute_block(b_type, b_content):
            all_matches = []
            
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
                    if name == "reply": data = {"msg_id": int(match.group(1))}
                    elif name == "react":
                        parts = [p.strip() for p in match.group(2).split("|")]
                        if len(parts) > 1:
                            data = {"msg_id": int(match.group(1)), "emoji": parts[0], "action": parts[1]}
                        else:
                            data = {"msg_id": int(match.group(1)), "emoji": parts[0], "action": "set"}
                    elif name == "attach": data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "edit": data = {"msg_id": int(match.group(1)), "text": match.group(2)}
                    elif name == "delete": data = {"msg_id": int(match.group(1))}
                    elif name == "noop": data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "tool": data = {"tool_name": match.group(1), "args_str": match.group(2)}
                    all_matches.append((match.start(), match.end(), name, data))
                    
            xml_regexes_compiled = [
                (re.compile(r'(?<!\\)<reply\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</reply>', re.IGNORECASE | re.DOTALL), "reply_msg"),
                (re.compile(r'(?<!\\)<react\s+([^>]*)\s*/?>', re.IGNORECASE), "react_tag"),
                (re.compile(r'(?<!\\)<attach\s+files=["\']([^"\']*)["\'](?:\s+caption=["\']([^"\']*)["\'])?\s*/?>', re.IGNORECASE), "attach"),
                (re.compile(r'(?<!\\)<attach\s+files=["\']([^"\']*)["\']>(.*?)</attach>', re.IGNORECASE | re.DOTALL), "attach_tag"),
                (re.compile(r'(?<!\\)<edit\s+(?:msg_)?id=["\'](\d+)["\']>(.*?)</edit>', re.IGNORECASE | re.DOTALL), "edit"),
                (re.compile(r'(?<!\\)<delete\s+(?:msg_)?id=["\'](\d+)["\']\s*/?>', re.IGNORECASE), "delete"),
                (re.compile(r'(?<!\\)<pin\s+(?:msg_)?id=["\'](\d+)["\'](?:\s+notify=["\'](true|false)["\'])?\s*/?>', re.IGNORECASE), "pin"),
                (re.compile(r'(?<!\\)<unpin(?:\s+(?:msg_)?id=["\'](\d+)["\'])?\s*/?>', re.IGNORECASE), "unpin"),
                (re.compile(r'(?<!\\)<(?:noop|no_op_ignore)\s+reason=["\']([^"\']*)["\'](?:\s+continue=["\'](true|false)["\'])?\s*/?>', re.IGNORECASE), "noop"),
                (re.compile(r'(?<!\\)<tool\s+name=["\']([a-zA-Z0-9_]+)["\']\s*([^>]*)\s*/?>', re.IGNORECASE), "tool")
            ]
            
            for regex, name in xml_regexes_compiled:
                for match in regex.finditer(b_content):
                    if name == "reply_msg": data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "react_tag":
                        attrs_str = match.group(1)
                        id_m = re.search(r'(?:msg_)?id=["\'](\d+)["\']', attrs_str, re.IGNORECASE)
                        emoji_m = re.search(r'emoji=["\']([^"\']*)["\']', attrs_str, re.IGNORECASE)
                        action_m = re.search(r'action=["\']([^"\']*)["\']', attrs_str, re.IGNORECASE)
                        data = {
                            "msg_id": int(id_m.group(1)) if id_m else None,
                            "emoji": emoji_m.group(1) if emoji_m else None,
                            "action": action_m.group(1) if action_m else "set"
                        }
                        name = "react"
                    elif name == "attach": data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2) or ""}
                    elif name == "attach_tag":
                        data = {"files": [f.strip() for f in match.group(1).split(",")], "caption": match.group(2).strip()}
                        name = "attach"
                    elif name == "edit": data = {"msg_id": int(match.group(1)), "text": match.group(2).strip()}
                    elif name == "delete": data = {"msg_id": int(match.group(1))}
                    elif name == "noop": data = {"reason": match.group(1), "continue": True if match.group(2) and match.group(2).lower() == "true" else False}
                    elif name == "tool": data = {"tool_name": match.group(1), "args_str": match.group(2)}
                    all_matches.append((match.start(), match.end(), name, data))

            if not all_matches:
                return

            all_matches.sort(key=lambda x: x[0])
            segments = [(name, data) for (start, end, name, data) in all_matches]
            
            async def execute_segment(segment):
                s_type, s_data = segment
                from utils import matches_filter
                from registry import tag_block_registry
                if not matches_filter(s_type, config.AI_TAG_WHITELIST, config.AI_TAG_BLACKLIST):
                    return
                handler_meta = tag_block_registry.get(s_type)
                if handler_meta:
                    try:
                        await handler_meta.callable(s_data, chat_entity, reply_to_id, chat_id, self.client, self.db)
                    except Exception:
                        pass

            if b_type == "seq":
                for segment in segments: await execute_segment(segment)
            elif b_type == "par":
                tasks_list = [execute_segment(segment) for segment in segments]
                await asyncio.gather(*tasks_list, return_exceptions=True)
            elif b_type == "bg":
                for segment in segments: asyncio.create_task(execute_segment(segment))

        # 3. Schedule execution of parsed blocks in background
        for b_type, b_content in blocks:
            asyncio.create_task(execute_block(b_type, b_content))

        # 4. Clean-strip all block wrappers and embedded tags from final string
        if not ranges_to_strip:
            clean_text = cleaned_text
            clean_text = re.sub(config.RE_REPLY_TAG, "", clean_text)
            clean_text = re.sub(config.RE_REACT_TAG, "", clean_text)
            clean_text = re.sub(config.RE_ATTACH_TAG, "", clean_text)
            clean_text = re.sub(config.RE_EDIT_TAG, "", clean_text)
            clean_text = re.sub(config.RE_DELETE_TAG, "", clean_text)
            clean_text = re.sub(config.RE_NOOP_TAG, "", clean_text)
            clean_text = re.sub(config.RE_TOOL_TAG, "", clean_text)
            clean_text = re.sub(r'<reply\s+(?:msg_)?id=["\']\d+["\']>(.*?)</reply>', "", clean_text, flags=re.IGNORECASE | re.DOTALL)
            clean_text = re.sub(r'<react\s+[^>]*\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<attach\s+files=["\']([^"\']*)["\'](?:\s+caption=["\']([^"\']*)["\'])?\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<attach\s+files=["\']([^"\']*)["\']>(.*?)</attach>', "", clean_text, flags=re.IGNORECASE | re.DOTALL)
            clean_text = re.sub(r'<edit\s+(?:msg_)?id=["\']\d+["\']>(.*?)</edit>', "", clean_text, flags=re.IGNORECASE | re.DOTALL)
            clean_text = re.sub(r'<delete\s+(?:msg_)?id=["\']\d+["\']\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<pin\s+[^>]*\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<unpin\s+[^>]*\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<noop\s+reason=["\']([^"\']*)["\'](?:\s+continue=["\'](?:true|false)["\'])?\s*/?>', "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<tool\s+name=["\']([a-zA-Z0-9_]+)["\']\s*([^>]*)\s*/?>', "", clean_text, flags=re.IGNORECASE)
            return clean_text.strip()

        clean_parts = []
        last_idx = 0
        for start, end in ranges_to_strip:
            clean_parts.append(cleaned_text[last_idx:start])
            last_idx = end
        clean_parts.append(cleaned_text[last_idx:])
        final_stripped = "".join(clean_parts).strip()
        final_stripped = final_stripped.replace(r'\[', '[').replace(r'\]', ']')
        final_stripped = final_stripped.replace(r'\<', '<').replace(r'\>', '>')
        return final_stripped
