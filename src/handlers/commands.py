from telegram import Update
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access
from src.db import get_stats, cancel_user_jobs
from src.queue_manager import get_queue_length
from src.utils.keyboard import get_main_reply_keyboard

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "👋 Welcome to Zestogram!\n\n"
        "Send me one or more Instagram links and I'll download them for you.\n"
        "Use the quick menu buttons below or send /help to see all commands.",
        reply_markup=get_main_reply_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "📚 *Help & Commands*\n\n"
        "Just send a message containing an Instagram link (like a Reel or Post).\n\n"
        "Commands / Quick Buttons:\n"
        "⚙️ /settings - Interactive settings menu\n"
        "🎵 /audio - Toggle audio-only mode\n"
        "📋 /queue - Check queue depth\n"
        "📊 /stats - View download stats\n"
        "🛑 /cancel - Cancel your pending queued jobs",
        parse_mode="Markdown",
        reply_markup=get_main_reply_keyboard()
    )

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    depth = await get_queue_length()
    await update.message.reply_text(f"📊 Current queue depth: {depth} pending job(s)")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    stats = await get_stats()
    await update.message.reply_text(
        f"📈 *Bot Statistics*\n\n"
        f"Total Success: {stats['total_success']}\n"
        f"Total Failed: {stats['total_failed']}",
        parse_mode="Markdown"
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
        
    user_id = update.effective_user.id
    count = await cancel_user_jobs(user_id)
    
    if count > 0:
        await update.message.reply_text(f"🛑 Cancelled {count} pending download(s).")
    else:
        await update.message.reply_text("No pending downloads to cancel.")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Attempt to clear recent messages in the chat by iterating to bypass user message permission errors."""
    if not await check_access(update, context):
        return
        
    chat_id = update.effective_chat.id
    current_msg_id = update.message.message_id
    
    status_msg = await update.message.reply_text("🧹 Attempting to clear recent bot messages...")
    
    deleted_count = 0
    # Look back at the last 30 messages to avoid severe rate limits
    for msg_id in range(current_msg_id, max(0, current_msg_id - 30), -1):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_count += 1
        except Exception:
            # Silently ignore messages we can't delete (like user messages)
            pass
            
    try:
        await status_msg.edit_text(
            f"✅ Cleared {deleted_count} recent bot messages.\n\n"
            "*(Note: I cannot delete your messages in a private chat. To fully wipe the chat, tap the three dots (⋮) and select Clear History.)*"
        )
    except Exception:
        pass
