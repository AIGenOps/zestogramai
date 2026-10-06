import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.config import config
from src.db import (
    get_system_stats,
    get_user_db,
    add_admin_db,
    remove_admin_db,
    is_admin_db,
    get_all_admins_db,
    get_pending_jobs_count
)
from src.services.membership import (
    get_user_membership,
    get_user_quota_info,
    grant_pro,
    grant_unlimited,
    revoke_membership,
    Plan
)
from src.handlers.commands import format_status_message

logger = logging.getLogger(__name__)

UNAUTHORIZED_MSG = "You are not authorized to use this command."

def is_owner(user_id: int) -> bool:
    if config.owner_id is None:
        return False
    return user_id == config.owner_id

async def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    if user_id in config.parsed_admin_user_ids:
        return True
    return await is_admin_db(user_id)

async def require_admin(user_id: int) -> bool:
    return await is_admin(user_id)

def require_owner(user_id: int) -> bool:
    return is_owner(user_id)

def _parse_target_id(context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    if not context.args or not context.args[0]:
        return None
    raw = context.args[0].strip()
    if raw.startswith("-"):
        clean = raw[1:]
    else:
        clean = raw
    if clean.isdigit():
        try:
            return int(raw)
        except ValueError:
            return None
    return None

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_admin(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return
        
    stats = await get_system_stats()
    text = (
        f"Users: {stats['total_users']:,}\n"
        f"Free: {stats['free_users']:,}\n"
        f"Pro: {stats['pro_users']:,}\n"
        f"Unlimited: {stats['unlimited_users']:,}\n\n"
        f"Successful downloads: {stats['successful_downloads']:,}\n"
        f"Downloads in current 24h: {stats['downloads_24h']:,}\n"
        f"Currently processing: {stats['currently_processing']:,}\n"
        f"Queued: {stats['queued']:,}\n"
        f"Failed: {stats['failed']:,}"
    )
    await update.message.reply_text(text)

async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_admin(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return
        
    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /user <telegram_id>")
        return

    user_row = await get_user_db(target_id)
    pending_count = await get_pending_jobs_count(target_id)
    
    from src.db import get_db_path, DB_PATH
    import aiosqlite
    has_jobs = False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM jobs WHERE user_id = ? LIMIT 1", (target_id,)) as cur:
            has_jobs = (await cur.fetchone()) is not None

    if not user_row and not has_jobs and pending_count == 0:
        await update.message.reply_text("User not found.")
        return

    quota_info = await get_user_quota_info(target_id)
    membership = await get_user_membership(target_id)
    
    status_text = format_status_message(quota_info, membership)
    response_text = f"User ID: {target_id}\n{status_text}"
    await update.message.reply_text(response_text)

async def grantpro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_admin(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /grantpro <telegram_id>")
        return

    membership = await get_user_membership(target_id)
    if membership.effective_plan == Plan.UNLIMITED:
        await update.message.reply_text(f"User {target_id} is Unlimited and already has higher priority.")
        return

    new_mem = await grant_pro(target_id, days=30)
    exp_dt = new_mem.pro_expires_at.astimezone(timezone.utc)
    exp_str = exp_dt.strftime("%d %b %Y, %H:%M")
    
    text = (
        f"Pro granted to {target_id}.\n"
        f"Pro expires: {exp_str}"
    )
    logger.info(f"Admin {user_id} granted Pro to {target_id}")
    await update.message.reply_text(text)

async def grantunlimited_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_admin(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /grantunlimited <telegram_id>")
        return

    await grant_unlimited(target_id)
    logger.info(f"Admin {user_id} granted Unlimited to {target_id}")
    await update.message.reply_text(f"Unlimited granted to {target_id}.")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_admin(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /revoke <telegram_id>")
        return

    await revoke_membership(target_id)
    logger.info(f"Admin {user_id} revoked membership for {target_id}")
    await update.message.reply_text(f"Membership revoked for {target_id}.")

async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not require_owner(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /addadmin <telegram_id>")
        return

    if await is_admin(target_id):
        await update.message.reply_text("User is already an admin.")
        return

    await add_admin_db(target_id)
    logger.info(f"Owner {user_id} added admin {target_id}")
    await update.message.reply_text(f"Admin added: {target_id}.")

async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not require_owner(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    target_id = _parse_target_id(context)
    if target_id is None:
        await update.message.reply_text("Usage: /removeadmin <telegram_id>")
        return

    if is_owner(target_id):
        await update.message.reply_text("Cannot remove owner.")
        return

    if not await is_admin_db(target_id):
        await update.message.reply_text("User is not an admin.")
        return

    await remove_admin_db(target_id)
    logger.info(f"Owner {user_id} removed admin {target_id}")
    await update.message.reply_text(f"Admin removed: {target_id}.")

async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not require_owner(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    db_admins = await get_all_admins_db()
    env_admins = [uid for uid in config.parsed_admin_user_ids if not is_owner(uid)]
    all_admins = list(dict.fromkeys(db_admins + env_admins))

    if not all_admins:
        await update.message.reply_text("No additional admins configured.")
        return

    lines = ["Admins:"] + [str(uid) for uid in all_admins]
    await update.message.reply_text("\n".join(lines))
