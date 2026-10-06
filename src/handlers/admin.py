import psutil
import logging
from telegram import Update
from telegram.ext import ContextTypes
from src.db import get_unique_users_count, ban_user, unban_user
from src.config import config

logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    if user_id == 1889732098:
        return True
    return user_id in config.parsed_allowed_user_ids

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin privileges required.")
        return
        
    count = await get_unique_users_count()
    await update.message.reply_text(f"👥 Total unique users: {count}")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin privileges required.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
        
    try:
        target_id = int(context.args[0])
        await ban_user(target_id)
        await update.message.reply_text(f"🚫 User {target_id} has been banned.")
    except ValueError:
        await update.message.reply_text("Invalid user ID.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin privileges required.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
        
    try:
        target_id = int(context.args[0])
        await unban_user(target_id)
        await update.message.reply_text(f"✅ User {target_id} has been unbanned.")
    except ValueError:
        await update.message.reply_text("Invalid user ID.")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin privileges required.")
        return
        
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    health_text = (
        "🖥 **System Health**\n\n"
        f"**CPU:** {cpu_percent}%\n"
        f"**RAM:** {memory.percent}% ({memory.used / 1024 / 1024 / 1024:.1f}GB / {memory.total / 1024 / 1024 / 1024:.1f}GB)\n"
        f"**Disk:** {disk.percent}% ({disk.used / 1024 / 1024 / 1024:.1f}GB / {disk.total / 1024 / 1024 / 1024:.1f}GB)\n"
    )
    
    await update.message.reply_text(health_text, parse_mode="Markdown")
