from telegram import Update
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access
from src.db import get_stats, cancel_user_jobs
from src.queue_manager import job_queue

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "👋 Welcome to Zestogram!\n\n"
        "Send me one or more Instagram links and I'll download them for you.\n"
        "Supports Reels, Posts, Carousels, IGTV, and Stories.\n\n"
        "Send /help to see all commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "📚 *Help & Commands*\n\n"
        "Just send a message containing an Instagram link (like a Reel or Post).\n\n"
        "Commands:\n"
        "/start - Greeting\n"
        "/help - Show this message\n"
        "/queue - Check queue depth\n"
        "/stats - View download stats\n"
        "/cancel - Cancel your pending queued jobs",
        parse_mode="Markdown"
    )

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    depth = job_queue.qsize()
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
    """Attempt to clear recent messages in the chat using bulk deletion."""
    if not await check_access(update, context):
        return
        
    chat_id = update.effective_chat.id
    current_msg_id = update.message.message_id
    
    # Send an initial status message
    status_msg = await update.message.reply_text("🧹 Attempting to clear recent messages...")
    
    # Try to delete the last 100 messages (Telegram limit per request)
    message_ids_to_delete = list(range(max(1, current_msg_id - 99), current_msg_id + 1))
    
    try:
        # We exclude the status message itself so it remains, or we can delete it too.
        # Let's try to bulk delete
        await context.bot.delete_messages(chat_id=chat_id, message_ids=message_ids_to_delete)
        # Send a brief self-destructing success message
        success_msg = await context.bot.send_message(chat_id=chat_id, text="✅ Chat cleared as much as Telegram allows (last 48 hours).")
        
        # Optionally schedule deleting the success message after 3 seconds
        import asyncio
        async def delete_later():
            await asyncio.sleep(3)
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=success_msg.message_id)
            except Exception:
                pass
        asyncio.create_task(delete_later())
        
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Could not completely clear chat. Telegram restricts bots to deleting messages sent within the last 48 hours.\n\nError: {str(e)}")
