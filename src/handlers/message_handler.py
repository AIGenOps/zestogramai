from telegram import Update
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access, enforce_rate_limit
from src.link_extractor import extract_instagram_urls
from src.db import add_job
from src.queue_manager import enqueue_job

logger = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    if not await check_access(update, context):
        return

    # Check for media attachments
    msg = update.message
    file_id = None
    file_name = None
    
    if msg.document:
        file_id = msg.document.file_id
        file_name = msg.document.file_name or "document"
    elif msg.video:
        file_id = msg.video.file_id
        file_name = msg.video.file_name or "video.mp4"
    elif msg.audio:
        file_id = msg.audio.file_id
        file_name = msg.audio.file_name or "audio.mp3"
        
    if file_id:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("🎥 Convert to MP4 Video", callback_data="convert_to:mp4")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.reply_text(
            f"File detected: `{file_name}`\nWould you like to convert it to standard MP4 video format?",
            reply_markup=reply_markup,
            parse_mode="Markdown",
            reply_to_message_id=msg.message_id
        )
        return

    # Existing URL logic
    text = msg.text
    if not text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Check for reply keyboard button clicks
    clean_text = text.strip()
    if clean_text in ("👥 Users", "users"):
        from src.handlers.admin import users_command
        await users_command(update, context)
        return
    elif clean_text in ("🖥 System Health", "health"):
        from src.handlers.admin import health_command
        await health_command(update, context)
        return
    elif clean_text in ("📊 Stats", "stats"):
        from src.handlers.commands import stats_command
        await stats_command(update, context)
        return
    elif clean_text in ("📋 Queue Depth", "📋 Queue", "queue"):
        from src.handlers.commands import queue_command
        await queue_command(update, context)
        return
    elif clean_text in ("⚙️ Settings", "settings"):
        from src.handlers.settings import settings_command
        await settings_command(update, context)
        return
    elif clean_text in ("❓ Help", "help"):
        from src.handlers.commands import help_command
        await help_command(update, context)
        return
    elif clean_text in ("🛑 Cancel Jobs", "🛑 Cancel", "cancel"):
        from src.handlers.commands import cancel_command
        await cancel_command(update, context)
        return
    elif clean_text in ("🧹 Clear Chat", "clear"):
        from src.handlers.commands import clear_command
        await clear_command(update, context)
        return
    
    urls = extract_instagram_urls(text)
    
    if not urls:
        if "/p/" in text:
            await update.message.reply_text("Instagram posts and carousels are no longer supported. Please send Reels only.")
        else:
            from src.utils.keyboard import get_reply_keyboard_for_user
            await update.message.reply_text(
                "Please send a valid video link (Instagram Reel, YouTube Short/Video, etc).",
                reply_markup=get_reply_keyboard_for_user(user_id)
            )
        return
        
    status_msg = await update.message.reply_text(
        f"**Processing {len(urls)} item(s)...**",
        parse_mode="Markdown",
        reply_to_message_id=update.message.message_id
    )
    
    from src.utils.queue_ui import batch_tracker
    jobs_info = []
    
    for url in urls:
        if not await enforce_rate_limit(user_id):
            await update.message.reply_text("⚠️ Rate limit exceeded. Please wait a minute before sending more links.")
            break
            
        job_id = await add_job(user_id, chat_id, status_msg.message_id, url)
        jobs_info.append({'job_id': job_id, 'url': url})
        
        await enqueue_job({
            'job_id': job_id,
            'user_id': user_id,
            'chat_id': chat_id,
            'url': url,
            'message_id': status_msg.message_id,
            'original_message_id': update.message.message_id,
            'is_batch': True
        })
        
    if jobs_info:
        batch_tracker.add_batch(chat_id, status_msg.message_id, jobs_info)
        for j in jobs_info:
            await batch_tracker.update_job(context.bot, status_msg.message_id, j['job_id'], 'queued')
