from telegram import Update
from telegram.ext import ContextTypes
import logging

from src.utils.access_control import check_access, enforce_rate_limit
from src.link_extractor import extract_instagram_urls
from src.db import add_job
from src.queue_manager import enqueue_job, job_queue

logger = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
        
    if not await check_access(update, context):
        return

    text = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    urls = extract_instagram_urls(text)
    
    if not urls:
        await update.message.reply_text("Please send a valid Instagram link (Reel, Post, IGTV, Carousel, or Story).")
        return
        
    for url in urls:
        if not await enforce_rate_limit(user_id):
            await update.message.reply_text("⚠️ Rate limit exceeded. Please wait a minute before sending more links.")
            break
            
        # Reply to user immediately to acknowledge
        status_msg = await update.message.reply_text(
            f"📥 Queued (position {job_queue.qsize() + 1})"
        )
        
        job_id = await add_job(user_id, chat_id, status_msg.message_id, url)
        
        enqueue_job({
            'job_id': job_id,
            'user_id': user_id,
            'chat_id': chat_id,
            'url': url,
            'message_id': status_msg.message_id,
            'original_message_id': update.message.message_id
        })
