import logging
import asyncio
import os
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from src.config import config
from src.db import init_db
from src.queue_manager import start_workers
from src.handlers.commands import start_command, help_command, queue_command, stats_command, cancel_command, clear_command
from src.handlers.settings import settings_command, audio_command, settings_callback
from src.handlers.message_handler import handle_message
from telegram import BotCommand

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, config.log_level.upper(), logging.INFO)
)

# Also log to file as requested in spec
log_dir = "/data/logs"
os.makedirs(log_dir, exist_ok=True)
file_handler = logging.FileHandler(os.path.join(log_dir, "bot.log"))
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)

async def post_init(application):
    await init_db()
    await start_workers(application)
    
    # Register commands for auto-complete menu
    commands = [
        BotCommand("start", "Greeting and basic info"),
        BotCommand("help", "List of commands and instructions"),
        BotCommand("settings", "Interactive settings menu"),
        BotCommand("audio", "Toggle audio-only mode"),
        BotCommand("queue", "Check download queue status"),
        BotCommand("stats", "View bot download statistics"),
        BotCommand("cancel", "Cancel your pending queued jobs"),
        BotCommand("clear", "Clear recent messages in chat")
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")
        
    logger.info("Database initialized and workers started.")

def main():
    if not config.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return
        
    builder = ApplicationBuilder().token(config.telegram_bot_token)
    
    if config.use_local_bot_api:
        logger.info("Using local Telegram Bot API server.")
        builder.base_url("http://telegram-bot-api:8081/bot")
        builder.base_file_url("http://telegram-bot-api:8081/file/bot")
        builder.local_mode(True)
        
    builder.post_init(post_init)
    
    app = builder.build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("clear", clear_command))
    
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(settings_callback))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot is starting up...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
