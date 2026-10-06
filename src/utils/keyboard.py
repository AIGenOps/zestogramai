from telegram import ReplyKeyboardMarkup, KeyboardButton

from telegram import ReplyKeyboardMarkup, KeyboardButton
from src.handlers.admin import is_admin

def get_reply_keyboard_for_user(user_id: int) -> ReplyKeyboardMarkup:
    """
    Returns persistent Reply Keyboard buttons appropriate for the user's role.
    General users see: Help, Cancel Jobs, Clear Chat.
    Admins also see: Users, System Health, Stats, Queue Depth.
    """
    if is_admin(user_id):
        keyboard = [
            [KeyboardButton("👥 Users"), KeyboardButton("🖥 System Health")],
            [KeyboardButton("📊 Stats"), KeyboardButton("📋 Queue Depth")],
            [KeyboardButton("❓ Help"), KeyboardButton("🛑 Cancel Jobs"), KeyboardButton("🧹 Clear Chat")]
        ]
    else:
        keyboard = [
            [KeyboardButton("❓ Help"), KeyboardButton("🛑 Cancel Jobs"), KeyboardButton("🧹 Clear Chat")]
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
