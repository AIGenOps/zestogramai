from telegram import ReplyKeyboardMarkup, KeyboardButton

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """
    Returns persistent Reply Keyboard buttons for quick command access.
    """
    keyboard = [
        [KeyboardButton("⚙️ Settings"), KeyboardButton("🎵 Toggle Audio")],
        [KeyboardButton("📊 Stats"), KeyboardButton("📋 Queue Depth")],
        [KeyboardButton("❓ Help"), KeyboardButton("🛑 Cancel Jobs")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
