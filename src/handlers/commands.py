from telegram import Update
from telegram.ext import ContextTypes
import logging
from datetime import datetime, timezone
from typing import Optional

from src.utils.access_control import check_access
from src.db import get_stats, cancel_user_jobs
from src.queue_manager import get_queue_length
from src.services.membership import get_user_quota_info, get_user_membership, Plan, QuotaInfo, UserMembership

logger = logging.getLogger(__name__)

CORE_HELP_MESSAGE = "Send me an Instagram Reel or YouTube Short link and I'll download it for you."

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(CORE_HELP_MESSAGE)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    await update.message.reply_text(CORE_HELP_MESSAGE)

def format_status_message(quota_info: QuotaInfo, membership: UserMembership, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
        
    lines = []
    lines.append(f"Plan: {membership.effective_plan.title()}")
    
    if membership.effective_plan == Plan.UNLIMITED:
        lines.append(f"Downloads: {quota_info.usage}")
        lines.append(f"Queue: {quota_info.pending_jobs} / 10")
        return "\n".join(lines)
        
    limit_int = int(quota_info.limit)
    lines.append(f"Downloads: {quota_info.usage} / {limit_int}")
    
    if quota_info.is_window_active and quota_info.window_reset_at and quota_info.window_reset_at > now:
        total_sec = max(0, int((quota_info.window_reset_at - now).total_seconds()))
        hours = total_sec // 3600
        mins = (total_sec % 3600) // 60
        lines.append(f"Reset: in {hours}h {mins}m")
        
    if membership.effective_plan == Plan.PRO and membership.pro_expires_at:
        exp_dt = membership.pro_expires_at
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        exp_dt = exp_dt.astimezone(timezone.utc)
        exp_str = exp_dt.strftime("%d %b %Y, %H:%M")
        lines.append(f"Pro expires: {exp_str}")
        
    lines.append(f"Queue: {quota_info.pending_jobs} / 10")
    
    if not quota_info.is_window_active:
        lines.append("Quota starts with your first successful download.")
        
    return "\n".join(lines)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE, now: Optional[datetime] = None):
    if not await check_access(update, context):
        return
    now = now or datetime.now(timezone.utc)
    user_id = update.effective_user.id
    
    quota_info = await get_user_quota_info(user_id, now=now)
    membership = await get_user_membership(user_id, now=now)
    
    msg_text = format_status_message(quota_info, membership, now=now)
    await update.message.reply_text(msg_text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
        
    user_id = update.effective_user.id
    count = await cancel_user_jobs(user_id)
    
    if count > 0:
        await update.message.reply_text(f"Cancelled {count} pending download{'s' if count != 1 else ''}.")
    else:
        await update.message.reply_text("There are no pending downloads to cancel.")

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    depth = await get_queue_length()
    await update.message.reply_text(f"Current queue depth: {depth} pending job(s)")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    stats = await get_stats()
    await update.message.reply_text(
        f"Bot Statistics\n\n"
        f"Total Success: {stats['total_success']}\n"
        f"Total Failed: {stats['total_failed']}"
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
        
    chat_id = update.effective_chat.id
    current_msg_id = update.message.message_id
    
    deleted_count = 0
    for msg_id in range(current_msg_id, max(0, current_msg_id - 30), -1):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_count += 1
        except Exception:
            pass
