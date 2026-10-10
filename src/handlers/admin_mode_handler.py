import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from src.utils.access_control import check_access
from src.config import config
from src.db import is_admin_db
from src.services.admin_mode import (
    get_admin_mode_state,
    set_mode_to_normal,
    request_or_switch_to_admin_mode,
    approve_user_submission,
    disapprove_user_submission,
)

logger = logging.getLogger(__name__)

def _build_mode_message(state: dict) -> tuple[str, InlineKeyboardMarkup]:
    current_mode = state.get("current_mode", "NORMAL")
    status = state.get("status", "NONE")

    status_labels = {
        "NONE": "Not requested",
        "PENDING": "Pending Admin Approval ⏳",
        "APPROVED": "Approved ✅",
        "DISAPPROVED": "Declined ❌"
    }
    status_text = status_labels.get(status, status)
    mode_display = "Send to Admin Mode" if current_mode == "ADMIN" else "Normal Download Mode"

    text = (
        "🔄 <b>Download Mode Selection</b>\n\n"
        "Choose how your downloaded reels should be handled:\n\n"
        "• <b>Normal Download Mode:</b> Videos are downloaded and sent directly to your private chat.\n"
        "• <b>Send to Admin Mode:</b> Videos are downloaded and sent to the admin group topic. Zero quota consumption.\n\n"
        f"<b>Current Active Mode:</b> {mode_display}\n"
        f"<b>Admin Mode Status:</b> {status_text}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'✅ ' if current_mode == 'NORMAL' else ''}📥 Normal Download Mode",
            callback_data="set_mode:normal"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current_mode == 'ADMIN' else ''}📤 Send to Admin Mode",
            callback_data="set_mode:admin"
        )]
    ])
    return text, keyboard

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return

    user_id = update.effective_user.id
    state = await get_admin_mode_state(user_id)
    text, keyboard = _build_mode_message(state)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def mode_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    user_id = user.id
    data = query.data

    if data == "set_mode:normal":
        await set_mode_to_normal(user_id)
        await query.answer("Switched to Normal Download Mode.")
    elif data == "set_mode:admin":
        user_info = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username
        }
        success, msg = await request_or_switch_to_admin_mode(context.bot, user_id, user_info)
        await query.answer(msg, show_alert=("⏳" in msg or not success))

    state = await get_admin_mode_state(user_id)
    text, keyboard = _build_mode_message(state)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramError:
        pass

async def admin_submission_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    admin_user = query.from_user
    data = query.data

    # Permission check: must be admin or supergroup administrator
    is_admin = False
    if admin_user.id in config.parsed_admin_user_ids or (config.owner_id and admin_user.id == config.owner_id):
        is_admin = True
    elif await is_admin_db(admin_user.id):
        is_admin = True
    elif query.message and query.message.chat_id == config.admin_forum_group_id:
        try:
            member = await context.bot.get_chat_member(chat_id=config.admin_forum_group_id, user_id=admin_user.id)
            if member.status in ("creator", "administrator"):
                is_admin = True
        except Exception:
            is_admin = True

    if not is_admin:
        await query.answer("You are not authorized to make this decision.", show_alert=True)
        return

    try:
        action, target_user_str = data.split(":", 1)
        target_user_id = int(target_user_str)
    except Exception:
        await query.answer("Invalid request data.")
        return

    admin_name = admin_user.first_name or "Admin"

    if action == "approve_sub":
        await approve_user_submission(context.bot, target_user_id, admin_name=admin_name)
        await query.answer("User approved!")
        try:
            await query.edit_message_text(
                f"✅ <b>Approved</b> for User <code>{target_user_id}</code> by {admin_name}.\n\n"
                f"Reels sent by this user will now be delivered to this topic.",
                parse_mode="HTML"
            )
        except TelegramError:
            pass

    elif action == "disapprove_sub":
        await query.answer("Submission disapproved. Topic will be deleted.")
        await disapprove_user_submission(context.bot, target_user_id, admin_name=admin_name)
