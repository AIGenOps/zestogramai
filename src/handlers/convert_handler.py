from telegram import Update
from telegram.ext import ContextTypes
from src.db import add_job
from src.queue_manager import enqueue_job
import logging

logger = logging.getLogger(__name__)

async def convert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    data = query.data
    if not data.startswith("convert_to:"):
        return
        
    await query.answer()
    
    target_format = data.split(":")[1]
    
    msg = query.message.reply_to_message
    if not msg:
        await query.edit_message_text("Original message with file not found.")
        return
        
    file_id = None
    if msg.document:
        file_id = msg.document.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.audio:
        file_id = msg.audio.file_id
        
    if not file_id:
        await query.edit_message_text("No file found in the original message.")
        return
        
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    virtual_url = f"convert:{target_format}:{file_id}"
    
    status_msg = await query.edit_message_text(f"📥 Queued for conversion to {target_format.upper()}")
    
    job_id = await add_job(user_id, chat_id, status_msg.message_id, virtual_url)
    
    await enqueue_job({
        'job_id': job_id,
        'user_id': user_id,
        'chat_id': chat_id,
        'url': virtual_url,
        'message_id': status_msg.message_id,
        'original_message_id': msg.message_id
    })
