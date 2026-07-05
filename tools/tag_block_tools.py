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
        unescaped_text = raw_text.replace(r'\[', '[').replace(r'\]', ']')
        unescaped_text = unescaped_text.replace(r'\<', '<').replace(r'\>', '>')
        formatted_html = safe_telegram_html(unescaped_text)
        
        target_reply_id = data.get("msg_id") or reply_to_id
        try:
            result = await client.send_message(chat_entity, formatted_html, reply_to=int(target_reply_id), parse_mode="html")
            await db.save_message(str(chat_id), "model", unescaped_text, msg_id=result.id)
            import bot
            bot.processed_msg_ids.add((int(chat_id), result.id))
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
    "noop": ("tag", handlers.noop),
    "no_op_ignore": ("tag", handlers.noop),
    "tool": ("tag", handlers.tool),
    "seq": ("block", handlers.seq),
    "par": ("block", handlers.par),
    "bg": ("block", handlers.bg)
}

def register_system_tags_blocks():
    """Registers all root tags, labels, and blocks into the global TagBlockRegistry."""
    from registry import tag_block_registry
    for name, (type_str, func) in ROOT_TAGS_BLOCKS.items():
        tag_block_registry.register(
            name=name,
            type_str=type_str,
            callable_func=func,
            description=getattr(func, "__doc__", ""),
            is_custom=False
        )
    logger.info(f"System tags and blocks registration complete! Count: {len(ROOT_TAGS_BLOCKS)}")
