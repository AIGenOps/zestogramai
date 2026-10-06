import os
import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.config import config
from src.services.membership import Plan, QuotaInfo, get_user_membership, get_user_quota_info

logger = logging.getLogger(__name__)

def format_payment_instructions(
    target_plan: str,
    user_id: int,
    quota_info: Optional[QuotaInfo] = None,
    now: Optional[datetime] = None
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    reset_str = "in 24h 0m"
    if quota_info and quota_info.window_reset_at and quota_info.window_reset_at > now:
        total_sec = max(0, int((quota_info.window_reset_at - now).total_seconds()))
        hours = total_sec // 3600
        mins = (total_sec % 3600) // 60
        reset_str = f"in {hours}h {mins}m"

    if target_plan == Plan.PRO:
        return (
            "You've reached your Free download limit.\n\n"
            f"Reset: {reset_str}\n\n"
            f"Pro: ${config.pro_price_usd}/month\n"
            "500 downloads per 24h\n\n"
            "To upgrade, pay using the payment details below and email your payment proof to the configured payment email with your Telegram user ID.\n\n"
            f"Payment Email: {config.payment_email}\n"
            f"Amount: ${config.pro_price_usd}\n"
            f"Telegram ID: {user_id}"
        )
    elif target_plan == Plan.UNLIMITED:
        return (
            "You've reached your Pro download limit.\n\n"
            f"Reset: {reset_str}\n\n"
            f"Unlimited: ${config.unlimited_price_usd} one-time\n"
            "Permanent access\n\n"
            "To upgrade, pay using the payment details below and email your payment proof to the configured payment email with your Telegram user ID.\n\n"
            f"Payment Email: {config.payment_email}\n"
            f"Amount: ${config.unlimited_price_usd}\n"
            f"Telegram ID: {user_id}"
        )
    else:
        raise ValueError(f"Unsupported plan for payment instructions: {target_plan}")

async def send_upgrade_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    now: Optional[datetime] = None
):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    membership = await get_user_membership(user_id, now=now)
    if membership.effective_plan == Plan.UNLIMITED:
        return

    quota_info = await get_user_quota_info(user_id, now=now)

    if membership.effective_plan == Plan.FREE:
        target_plan = Plan.PRO
    else:
        target_plan = Plan.UNLIMITED

    text = format_payment_instructions(target_plan, user_id, quota_info=quota_info, now=now)

    qr_path = config.payment_qr_path
    if qr_path:
        if os.path.isfile(qr_path):
            try:
                with open(qr_path, "rb") as f:
                    await update.message.reply_photo(photo=f, caption=text)
                    return
            except Exception as e:
                logger.warning(f"Could not send local payment QR image from '{qr_path}': {e}")
        elif qr_path.startswith(("http://", "https://")):
            try:
                await update.message.reply_photo(photo=qr_path, caption=text)
                return
            except Exception as e:
                logger.warning(f"Could not send payment QR image from URL '{qr_path}': {e}")

    await update.message.reply_text(text)
