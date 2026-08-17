# tools/tag_block_tools.py
import os
import json
import asyncio
import logging
import inspect
import re
from typing import Any, List

import config
import tools
from registry import tag_block_registry
from utils import safe_telegram_html

logger = logging.getLogger("Tools.TagBlock")

class RootTagBlockHandlers:
    async def reply(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends a text message reply to a specific Telegram message."""
        raw_text = data.get("text", "")
        
        if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
            logger.info(f"Stripping nested XML/HTML tags and executing sub-actions for reply text: '{raw_text[:60]}...'")
            raw_text = await tools.ai_manager.executor.parse_execute_and_strip_tags(raw_text, chat_entity, reply_to_id, chat_id)
            
        unescaped_text = raw_text.replace(r'\[', '[').replace(r'\]', ']')
        unescaped_text = unescaped_text.replace(r'\<', '<').replace(r'\>', '>')
        formatted_html = safe_telegram_html(unescaped_text)
        
        if not formatted_html.strip():
            logger.info("Reply text is empty or fully stripped of action tags. Skipping sending empty text bubble.")
            return
            
        target_reply_id = data.get("msg_id") or reply_to_id
        try:
            try:
                target_reply_id = int(target_reply_id)
            except (ValueError, TypeError):
                target_reply_id = int(reply_to_id) if reply_to_id else None
                
            logger.info(f"Delivering text reply to msg #{target_reply_id} in chat {chat_id}: '{formatted_html[:60]}...'")
            from utils import send_message_safe
            sent_msgs = await send_message_safe(client, chat_entity, formatted_html, reply_to=target_reply_id, parse_mode="html")
            result = sent_msgs[-1] if sent_msgs else None
            
            reply_meta = ""
            try:
                from parser import parse_reply_metadata
                reply_meta = await parse_reply_metadata(result, chat_id, client, db)
            except Exception as e_meta:
                logger.debug(f"Failed to generate reply metadata for bot's own message: {str(e_meta)}")
            
            full_saved_text = f"{reply_meta}{unescaped_text}".strip()
            await db.save_message(str(chat_id), "model", full_saved_text, msg_id=result.id)
            for sm in sent_msgs:
                if hasattr(sm, "id"):
                    tools.processed_msg_ids.add((int(chat_id), sm.id))
        except Exception as tg_err:
            logger.error(f"Failed to deliver reply msg: {str(tg_err)}")

    async def react(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sets or removes an emoji reaction on a message."""
        emoji = data.get("emoji")
        msg_id = data.get("msg_id") or reply_to_id
        if emoji and emoji.lower() == "none":
            await tools.toolkit.set_message_reaction(chat_entity, msg_id, action="clear")
        else:
            await tools.toolkit.set_message_reaction(chat_entity, msg_id, reaction_emojis=emoji, action="set")

    async def attach(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends media attachments (photos, videos, files, GIFs) to the chat."""
        files = data.get("files") or []
        caption = data.get("caption") or ""
        await tools.toolkit.send_media_message(chat_id=chat_entity, files=files, caption=caption)

    async def rich_message(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends a structured Rich Message (article) with text, photos, maps, and collages."""
        blocks = data.get("blocks") or data.get("text")
        await tools.toolkit.send_rich_message(blocks_json=blocks, chat_id=chat_id, reply_to_msg_id=reply_to_id)

    async def edit(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Edits a previously sent own message in the chat."""
        msg_id = data.get("msg_id")
        text = data.get("text") or ""
        if msg_id:
            await tools.toolkit.edit_message(chat_entity, int(msg_id), text)

    async def delete(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Deletes a message in the chat."""
        msg_id = data.get("msg_id")
        if msg_id:
            await tools.toolkit.delete_message(chat_entity, int(msg_id))

    async def pin(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Pins a specific message in the chat."""
        msg_id = data.get("msg_id") or reply_to_id
        notify = data.get("notify", False)
        await tools.toolkit.pin_telegram_message(message_id=msg_id, chat_id=chat_entity, notify=notify)

    async def unpin(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Unpins a message in the chat."""
        msg_id = data.get("msg_id")
        await tools.toolkit.unpin_telegram_message(message_id=msg_id, chat_id=chat_entity)

    async def noop(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """No-Op Ignore action. Specify a reason."""
        reason = data.get("reason", "No reason provided")
        continue_loop = data.get("continue", False)
        tools.toolkit.no_op_ignore(reason, continue_loop=continue_loop)

    async def header(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends a bold underlined header text block."""
        title = data.get("text") or data.get("title") or ""
        if title:
            from utils import safe_telegram_html, send_message_safe
            formatted = f"<b><u>{safe_telegram_html(title)}</u></b>"
            await send_message_safe(client, chat_entity, formatted, parse_mode="html", reply_to=reply_to_id)

    async def details(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends a collapsible details / expandable quote block."""
        title = data.get("title") or data.get("summary") or "Details"
        content = data.get("text") or data.get("content") or ""
        if content:
            from utils import safe_telegram_html, send_message_safe
            formatted = f"<details><summary>{safe_telegram_html(title)}</summary>{safe_telegram_html(content)}</details>"
            await send_message_safe(client, chat_entity, formatted, parse_mode="html", reply_to=reply_to_id)

    async def map_location(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends an embedded map geolocation link block."""
        lat = data.get("lat") or data.get("latitude") or "0"
        lon = data.get("lon") or data.get("longitude") or "0"
        caption = data.get("text") or data.get("caption") or ""
        await tools.toolkit.send_geolocation(latitude=float(lat), longitude=float(lon), chat_id=chat_id, caption=caption, reply_to_msg_id=reply_to_id)

    async def collage(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sends a multi-photo/video collage album."""
        files = data.get("files") or []
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        caption = data.get("text") or data.get("caption") or ""
        if files:
            await tools.toolkit.send_media_message(chat_id=chat_id, files=files, caption=caption, reply_to_msg_id=reply_to_id)

    async def article(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Parses an <article> XML container tag and sends it as a single Rich Message."""
        raw_inner = data.get("text", "")
        if not raw_inner:
            return

        import re
        blocks = []
        child_regex = re.compile(
            r'<([a-zA-Z0-9_]+)(?:\s+((?:"[^"]*"|\'[^\']*\'|[^>])*))?>(.*?)</\1>|<([a-zA-Z0-9_]+)\s+((?:"[^"]*"|\'[^\']*\'|[^>])*)\s*/>',
            re.IGNORECASE | re.DOTALL
        )

        last_idx = 0
        for match in child_regex.finditer(raw_inner):
            start_pos, end_pos = match.span()
            before_text = raw_inner[last_idx:start_pos].strip()
            if before_text:
                blocks.append({"type": "paragraph", "text": before_text})

            tag_name = (match.group(1) or match.group(4)).lower()
            attrs_str = match.group(2) or match.group(5) or ""
            content = (match.group(3) or "").strip()

            attrs = {}
            attr_pattern = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.IGNORECASE)
            for attr_match in attr_pattern.finditer(attrs_str):
                k = attr_match.group(1)
                v = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
                attrs[k] = v

            if tag_name in ["header", "h1", "h2", "h3"]:
                blocks.append({"type": "header", "title": content or attrs.get("title", "")})
            elif tag_name in ["p", "paragraph"]:
                blocks.append({"type": "paragraph", "text": content})
            elif tag_name in ["details", "summary"]:
                blocks.append({"type": "details", "title": attrs.get("title", "Details"), "content": content})
            elif tag_name in ["map", "geo", "geolocation"]:
                blocks.append({
                    "type": "map",
                    "latitude": attrs.get("lat") or attrs.get("latitude") or "0",
                    "longitude": attrs.get("lon") or attrs.get("longitude") or "0",
                    "caption": content or attrs.get("caption", "")
                })
            elif tag_name in ["collage", "album"]:
                files_list = attrs.get("files", "").split(",") if attrs.get("files") else []
                blocks.append({"type": "collage", "files": [f.strip() for f in files_list if f.strip()]})

            last_idx = end_pos

        after_text = raw_inner[last_idx:].strip()
        if after_text:
            blocks.append({"type": "paragraph", "text": after_text})

        if blocks:
            await tools.toolkit.send_rich_message(blocks_json=blocks, chat_id=chat_id, reply_to_msg_id=reply_to_id)

    async def tool(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Executes a registered AI tool dynamically by name."""
        t_name = data.get("tool_name") or data.get("key") or data.get("name")
        t_args_str = data.get("args_str") or ""
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
                    
        from registry import registry
        tool_meta = registry.get(t_name)
        if tool_meta:
            try:
                if inspect.iscoroutinefunction(tool_meta.callable):
                    tool_res = await tool_meta.callable(**t_args)
                else:
                    tool_res = tool_meta.callable(**t_args)
                await db.save_message(str(chat_id), "user", f"[System: Tool '{t_name}' executed. Result: {tool_res}]")
            except Exception as terr:
                logger.error(f"Error executing tool label {t_name}: {str(terr)}")

    async def mute(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        user_id = data.get("id") or data.get("user_id")
        duration = data.get("duration")
        if duration:
            try: duration = int(duration)
            except (ValueError, TypeError): duration = None
        if user_id:
            await tools.toolkit.mute_user(user_id=user_id, chat_id=chat_id, duration_seconds=duration)

    async def unmute(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        user_id = data.get("id") or data.get("user_id")
        if user_id:
            await tools.toolkit.unrestrict_user(user_id=user_id, chat_id=chat_id)

    async def kick(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        user_id = data.get("id") or data.get("user_id")
        if user_id:
            await tools.toolkit.kick_user(user_id=user_id, chat_id=chat_id)

    async def ban(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        user_id = data.get("id") or data.get("user_id")
        duration = data.get("duration")
        if duration:
            try: duration = int(duration)
            except (ValueError, TypeError): duration = None
        if user_id:
            await tools.toolkit.ban_user(user_id=user_id, chat_id=chat_id, duration_seconds=duration)

    async def unban(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        user_id = data.get("id") or data.get("user_id")
        if user_id:
            await tools.toolkit.unrestrict_user(user_id=user_id, chat_id=chat_id)

    async def search(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        query = data.get("query")
        if query:
            res = await tools.toolkit.internet_search(query=query)
            await db.save_message(str(chat_id), "user", f"[System: Search results for '{query}']: {res}")

    async def mediasearch(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        query = data.get("query")
        m_type = data.get("type", "image")
        limit_val = data.get("max_results") or data.get("limit")
        max_results = None
        if limit_val is not None:
            try: max_results = int(limit_val)
            except ValueError: pass
        if query:
            res = await tools.toolkit.internet_media_search(query=query, media_type=m_type, max_results=max_results)
            await db.save_message(str(chat_id), "user", f"[System: Media search results for '{query}']: {res}")

    async def draw(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        prompt = data.get("prompt")
        if prompt:
            res = await tools.toolkit.generate_image(prompt=prompt)
            await db.save_message(str(chat_id), "user", f"[System: Image generation results for '{prompt}']: {res}")

    async def seq(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs): pass
    async def par(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs): pass
    async def bg(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs): pass

handlers = RootTagBlockHandlers()

ROOT_TAGS_BLOCKS = {
    "reply": ("tag", handlers.reply),
    "reply_msg": ("tag", handlers.reply),
    "msg": ("tag", handlers.reply),
    "react": ("tag", handlers.react),
    "attach": ("tag", handlers.attach),
    "rich_message": ("tag", handlers.rich_message),
    "article": ("tag", handlers.article),
    "rich_post": ("tag", handlers.article),
    "header": ("tag", handlers.header),
    "h1": ("tag", handlers.header),
    "h2": ("tag", handlers.header),
    "details": ("tag", handlers.details),
    "summary": ("tag", handlers.details),
    "map": ("tag", handlers.map_location),
    "geo": ("tag", handlers.map_location),
    "collage": ("tag", handlers.collage),
    "album": ("tag", handlers.collage),
    "edit": ("tag", handlers.edit),
    "delete": ("tag", handlers.delete),
    "pin": ("tag", handlers.pin),
    "unpin": ("tag", handlers.unpin),
    "mute": ("tag", handlers.mute),
    "unmute": ("tag", handlers.unmute),
    "kick": ("tag", handlers.kick),
    "ban": ("tag", handlers.ban),
    "unban": ("tag", handlers.unban),
    "search": ("tag", handlers.search),
    "mediasearch": ("tag", handlers.mediasearch),
    "draw": ("tag", handlers.draw),
    "noop": ("tag", handlers.noop),
    "no_op_ignore": ("tag", handlers.noop),
    "idi_nahuy": ("tag", handlers.noop),
    "ignore_this_eblan": ("tag", handlers.noop),
    "tool": ("tag", handlers.tool),
    "tool_name": ("tag", handlers.tool),
    "seq": ("block", handlers.seq),
    "par": ("block", handlers.par),
    "bg": ("block", handlers.bg)
}

def create_generic_tag_handler(tool_meta):
    """Dynamically wraps a system/custom tool into an asynchronous XML tag handler."""
    async def tag_handler(data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        logger.info(f"Tag-to-Tool mapping triggered: <{tool_meta.name}>")
        call_args = {}
        sig = inspect.signature(tool_meta.callable)
        
        for param_name, param in sig.parameters.items():
            if param_name in data:
                val = data[param_name]
                if isinstance(val, str) and ((val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]"))):
                    try:
                        val = json.loads(val)
                    except Exception: pass
                if param.annotation == int:
                    try: val = int(val)
                    except ValueError: pass
                elif param.annotation == float:
                    try: val = float(val)
                    except ValueError: pass
                elif param.annotation == bool:
                    val = str(val).lower() in ["true", "1", "yes"]
                call_args[param_name] = val
            elif param_name == "chat_id":
                call_args["chat_id"] = chat_id
            elif param_name in ["message_id", "msg_id"]:
                if "msg_id" in data: call_args[param_name] = int(data["msg_id"])
                elif "id" in data: call_args[param_name] = int(data["id"])
                elif reply_to_id: call_args[param_name] = int(reply_to_id)
            elif param_name == "user_id":
                if "id" in data: call_args["user_id"] = data["id"]
                elif "user_id" in data: call_args["user_id"] = data["user_id"]
            elif param_name == "text" and "text" in data:
                call_args["text"] = data["text"]
            elif param_name == "query" and "text" in data:
                call_args["query"] = data["text"]
            elif param_name == "prompt" and "text" in data:
                call_args["prompt"] = data["text"]
            elif param_name == "code" and "text" in data:
                call_args["code"] = data["text"]
            elif param_name == "sql" and "text" in data:
                call_args["sql"] = data["text"]
            elif param_name == "filename" and "file" in data:
                call_args["filename"] = data["file"]
                
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            for k, v in data.items():
                if k not in call_args and k not in ["text", "msg_id", "id"]:
                    call_args[k] = v
                    
        try:
            if inspect.iscoroutinefunction(tool_meta.callable):
                res = await tool_meta.callable(**call_args)
            else:
                res = tool_meta.callable(**call_args)
            logger.info(f"Generic tag-to-tool <{tool_meta.name}> executed successfully.")
            if res and db:
                await db.save_message(str(chat_id), "user", f"[System: Tag <{tool_meta.name}> executed. Result: {res}]")
        except Exception as err:
            logger.error(f"Error executing generic tag <{tool_meta.name}>: {str(err)}")
            
    return tag_handler

def register_system_tags_blocks():
    """Registers all root tags, labels, and blocks into the global TagBlockRegistry."""
    from registry import tag_block_registry
    from registry import registry as tool_registry
    
    for name, (type_str, func) in ROOT_TAGS_BLOCKS.items():
        tag_block_registry.register(
            name=name,
            type_str=type_str,
            callable_func=func,
            description=getattr(func, "__doc__", ""),
            is_custom=False
        )
        
    added_tags_count = 0
    aliases = {
        "python": "execute_python_code",
        "sql": "execute_sql_query",
        "scrape": "scrape_url",
        "deepsearch": "internet_deep_search",
        "search": "internet_search",
        "mediasearch": "internet_media_search",
        "draw": "generate_image",
        "voice": "generate_audio",
        "video": "generate_video",
        "idi_nahuy": "no_op_ignore",
        "ignore_this_eblan": "no_op_ignore",
    }
    
    for tool_meta in tool_registry.get_all_tools():
        if tool_meta.name not in tag_block_registry._registry:
            handler = create_generic_tag_handler(tool_meta)
            tag_block_registry.register(
                name=tool_meta.name,
                type_str="tag",
                callable_func=handler,
                description=tool_meta.description,
                is_custom=False
            )
            added_tags_count += 1
            
    for alias_name, target_tool_name in aliases.items():
        target_tool = tool_registry.get(target_tool_name)
        if target_tool and alias_name not in tag_block_registry._registry:
            handler = create_generic_tag_handler(target_tool)
            tag_block_registry.register(
                name=alias_name,
                type_str="tag",
                callable_func=handler,
                description=target_tool.description,
                is_custom=False
            )
            added_tags_count += 1
            
    logger.info(f"System tags and blocks registration complete! Core: {len(ROOT_TAGS_BLOCKS)}, Dynamically Wrapped: {added_tags_count}")
