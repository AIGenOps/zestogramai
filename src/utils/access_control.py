from telegram import Update
from telegram.ext import ContextTypes
import logging
from src.config import config
from src.db import check_rate_limit, record_rate_limit, is_user_banned

logger = logging.getLogger(__name__)

async def is_user_allowed(user_id: int) -> bool:
    allowed_users = config.parsed_allowed_user_ids
    if allowed_users and user_id not in allowed_users:
        return False
    return True

async def is_chat_allowed(chat_id: int) -> bool:
    allowed_chats = config.parsed_allowed_chat_ids
    if allowed_chats and chat_id not in allowed_chats:
        return False
    return True

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if the user/chat is allowed to use the bot.
    Sends a warning message if they are not allowed (and avoids spamming by only warning once, 
    but for simplicity we just return False here and let the handler deal with it).
    """
    if not update.effective_user or not update.effective_chat:
        return False
        
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not await is_user_allowed(user_id):
        logger.info(f"Unauthorized access attempt by user {user_id}")
        await context.bot.send_message(chat_id=chat_id, text="⛔ You are not authorized to use this bot.")
        return False
        
    if not await is_chat_allowed(chat_id):
        logger.info(f"Unauthorized access attempt in chat {chat_id}")
        await context.bot.send_message(chat_id=chat_id, text="⛔ This chat is not authorized to use this bot.")
        return False
        
    if await is_user_banned(user_id):
        logger.info(f"Banned user access attempt by {user_id}")
        return False
        
    return True

async def enforce_rate_limit(user_id: int) -> bool:
    """
    Returns True if the user is within rate limits, False otherwise.
    """
    allowed = await check_rate_limit(user_id, config.max_jobs_per_user_per_minute)
    if allowed:
        await record_rate_limit(user_id)
    return allowed
