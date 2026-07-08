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
        
        # Strip any nested tag structures from the reply text recursively to avoid leaking tags in message bubbles
        if tools.ai_manager and hasattr(tools.ai_manager, "executor"):
            logger.info(f"Stripping nested XML/HTML tags and executing sub-actions for reply text: '{raw_text[:60]}...'")
            raw_text = await tools.ai_manager.executor.parse_execute_and_strip_tags(raw_text, chat_entity, reply_to_id, chat_id)
            
        unescaped_text = raw_text.replace(r'\[', '[').replace(r'\]', ']')
        unescaped_text = unescaped_text.replace(r'\<', '<').replace(r'\>', '>')
        formatted_html = safe_telegram_html(unescaped_text)
        
        # Stop execution of empty replies (e.g. if the tag text only contained a nested media tag which got executed and stripped)
        if not formatted_html.strip():
            logger.info("Reply text is empty or fully stripped of action tags. Skipping sending empty text bubble.")
            return
            
        target_reply_id = data.get("msg_id") or reply_to_id
        try:
            logger.info(f"Delivering text reply to msg #{target_reply_id} in chat {chat_id}: '{formatted_html[:60]}...'")
            result = await client.send_message(chat_entity, formatted_html, reply_to=int(target_reply_id), parse_mode="html")
            
            # Generate reply metadata for the bot's own message to preserve full context of her responses in database history
            reply_meta = ""
            try:
                from parser import parse_reply_metadata
                reply_meta = await parse_reply_metadata(result, chat_id, client, db)
            except Exception as e_meta:
                logger.debug(f"Failed to generate reply metadata for bot's own message: {str(e_meta)}")
            
            full_saved_text = f"{reply_meta}{unescaped_text}".strip()
            await db.save_message(str(chat_id), "model", full_saved_text, msg_id=result.id)
            tools.processed_msg_ids.add((int(chat_id), result.id))
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

    async def tool(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Executes a registered AI tool dynamically by name."""
        t_name = data.get("tool_name")
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
        """Mutes a user in the chat."""
        user_id = data.get("id")
        duration = int(data.get("duration")) if data.get("duration") else None
        if user_id:
            await tools.toolkit.mute_user(user_id=user_id, chat_id=chat_id, duration_seconds=duration)

    async def unmute(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Lifts restrictions from a user."""
        user_id = data.get("id")
        if user_id:
            await tools.toolkit.unrestrict_user(user_id=user_id, chat_id=chat_id)

    async def kick(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Kicks a user from the chat."""
        user_id = data.get("id")
        if user_id:
            await tools.toolkit.kick_user(user_id=user_id, chat_id=chat_id)

    async def ban(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Bans a user in the chat."""
        user_id = data.get("id")
        duration = int(data.get("duration")) if data.get("duration") else None
        if user_id:
            await tools.toolkit.ban_user(user_id=user_id, chat_id=chat_id, duration_seconds=duration)

    async def unban(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Unbans a user in the chat."""
        user_id = data.get("id")
        if user_id:
            await tools.toolkit.unrestrict_user(user_id=user_id, chat_id=chat_id)

    async def search(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Performs a web search and logs results to context."""
        query = data.get("query")
        if query:
            res = await tools.toolkit.internet_search(query=query)
            await db.save_message(str(chat_id), "user", f"[System: Search results for '{query}']: {res}")

    async def mediasearch(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Performs a media search and logs results to context."""
        query = data.get("query")
        m_type = data.get("type", "image")
        if query:
            res = await tools.toolkit.internet_media_search(query=query, media_type=m_type)
            await db.save_message(str(chat_id), "user", f"[System: Media search results for '{query}']: {res}")

    async def draw(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Generates an image and logs results to context."""
        prompt = data.get("prompt")
        if prompt:
            res = await tools.toolkit.generate_image(prompt=prompt)
            await db.save_message(str(chat_id), "user", f"[System: Image generation results for '{prompt}']: {res}")

    # Core blocks execution handlers (Stubs for structural metadata)
    async def seq(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Sequential Block Container."""
        pass

    async def par(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Parallel Block Container."""
        pass

    async def bg(self, data: dict, chat_entity, reply_to_id: int, chat_id: str, client, db, **kwargs):
        """Background Block Container."""
        pass

# Instantiate handlers
handlers = RootTagBlockHandlers()

ROOT_TAGS_BLOCKS = {
    "reply": ("tag", handlers.reply),
    "reply_msg": ("tag", handlers.reply),
    "msg": ("tag", handlers.reply),
    "react": ("tag", handlers.react),
    "attach": ("tag", handlers.attach),
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
    "tool": ("tag", handlers.tool),
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
        
        # Map attributes dynamically to fit the target tool signature
        for param_name, param in sig.parameters.items():
            if param_name in data:
                val = data[param_name]
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
                if "msg_id" in data:
                    call_args[param_name] = int(data["msg_id"])
                elif "id" in data:
                    call_args[param_name] = int(data["id"])
                elif reply_to_id:
                    call_args[param_name] = int(reply_to_id)
            elif param_name == "user_id":
                if "id" in data:
                    call_args["user_id"] = data["id"]
                elif "user_id" in data:
                    call_args["user_id"] = data["user_id"]
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
                
        # Append residual attributes if tool allows arbitrary kwargs
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
        
    # Dynamically register all tools as XML tags
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
            
    # Register convenient shorthand tag aliases
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