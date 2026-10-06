from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access
from src.db import get_user_settings, update_user_setting

logger = logging.getLogger(__name__)

async def _build_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    settings = await get_user_settings(user_id)
    
    cleanup_text = "✅ Auto-Cleanup" if settings.get("auto_cleanup") else "❌ Auto-Cleanup"
    
    keyboard = [
        [InlineKeyboardButton(cleanup_text, callback_data="toggle_auto_cleanup")],
        [InlineKeyboardButton("Done", callback_data="settings_done")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
        
    user_id = update.effective_user.id
    keyboard = await _build_settings_keyboard(user_id)
    
    await update.message.reply_text(
        "⚙️ **Your Settings**\n\nTap a button to toggle the setting:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download audio directly from a provided URL."""
    if not await check_access(update, context):
        return
        
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text("Usage: `/audio <url>` to download just the audio from a link.", parse_mode="Markdown")
        return
        
    from src.link_extractor import extract_instagram_urls
    from src.db import add_job
    from src.queue_manager import enqueue_job
    from src.utils.access_control import enforce_rate_limit
    
    text = " ".join(context.args)
    urls = extract_instagram_urls(text)
    
    if not urls:
        await update.message.reply_text("Please provide a valid Instagram or YouTube link after /audio.")
        return
        
    chat_id = update.effective_chat.id
    for url in urls:
        if not await enforce_rate_limit(user_id):
            await update.message.reply_text("⚠️ Rate limit exceeded. Please wait a minute before sending more links.")
            break
            
        status_msg = await update.message.reply_text(
            f"📥 Queued for Audio Extraction"
        )
        
        job_id = await add_job(user_id, chat_id, status_msg.message_id, url)
        
        await enqueue_job({
            'job_id': job_id,
            'user_id': user_id,
            'chat_id': chat_id,
            'url': url,
            'message_id': status_msg.message_id,
            'original_message_id': update.message.message_id,
            'force_audio': True
        })

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "settings_done":
        await query.edit_message_text("Settings saved. ✅")
        return
        
    settings = await get_user_settings(user_id)
    
    if data == "toggle_auto_cleanup":
        new_val = not settings.get("auto_cleanup")
        await update_user_setting(user_id, "auto_cleanup", new_val)
        
    # Rebuild keyboard and update message
    keyboard = await _build_settings_keyboard(user_id)
    await query.edit_message_reply_markup(reply_markup=keyboard)
