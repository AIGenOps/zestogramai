from telegram import Update
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access, enforce_rate_limit
from src.link_extractor import parse_text_urls
from src.db import add_job, update_job_status, update_job_message_id
from src.queue_manager import enqueue_job
from src.services.membership import submit_job_if_allowed

logger = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    if not await check_access(update, context):
        return

    text = update.message.text
    if not text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    clean_text = text.strip()

    # Check for command aliases or reply keyboard buttons
    if clean_text in ("❓ Help", "help"):
        from src.handlers.commands import help_command
        await help_command(update, context)
        return
    elif clean_text in ("status", "/status"):
        from src.handlers.commands import status_command
        await status_command(update, context)
        return
    elif clean_text in ("🛑 Cancel Jobs", "🛑 Cancel", "cancel"):
        from src.handlers.commands import cancel_command
        await cancel_command(update, context)
        return

    elif clean_text in ("🧹 Clear Chat", "clear"):
        from src.handlers.commands import clear_command
        await clear_command(update, context)
        return
    elif clean_text in ("👥 Users", "users"):
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
    
    supported_urls, unsupported_urls = parse_text_urls(text)
    
    if not supported_urls:
        if unsupported_urls:
            await update.message.reply_text("This link is not supported.")
        else:
            await update.message.reply_text("Send me an Instagram Reel or YouTube Short link and I'll download it for you.")
        return

    # Process supported URLs
    for url in supported_urls:
        if not await enforce_rate_limit(user_id):
            await update.message.reply_text("Rate limit exceeded. Please wait a minute before sending more links.")
            break
            
        allowed, reason, job_id = await submit_job_if_allowed(
            user_id=user_id,
            chat_id=chat_id,
            message_id=0,
            url=url,
            original_message_id=update.message.message_id
        )
        
        if not allowed:
            if "Quota limit" in reason or reason == "QUOTA_EXHAUSTED":
                from src.services.payment import send_upgrade_prompt
                await send_upgrade_prompt(update, context, user_id)
            else:
                await update.message.reply_text(reason)
            break
            
        # Send temporary processing message ONLY for accepted jobs
        processing_msg = await update.message.reply_text("Processing your video...")
        
        msg_id = getattr(processing_msg, 'message_id', None)
        if isinstance(msg_id, int):
            await update_job_message_id(job_id, msg_id)
        await update_job_status(job_id, 'queued')
        
        try:
            await enqueue_job({
                'job_id': job_id,
                'user_id': user_id,
                'chat_id': chat_id,
                'url': url,
                'message_id': processing_msg.message_id,
                'original_message_id': update.message.message_id,
                'is_batch': False
            })
        except Exception as e:
            logger.error(f"Failed to enqueue job {job_id}: {e}")
            await update_job_status(job_id, 'failed', f"Queue error: {e}")
            await update.message.reply_text("Unable to process this video. Please try again.")
            break

    if unsupported_urls and supported_urls:
        await update.message.reply_text("This link is not supported.")
