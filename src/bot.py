import logging
import asyncio
import os
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from src.config import config, get_data_dir
from src.db import init_db
from src.queue_manager import start_workers
from src.handlers.commands import start_command, help_command, queue_command, stats_command, cancel_command, clear_command, status_command
from src.handlers.settings import settings_command, settings_callback
from src.handlers.message_handler import handle_message
from telegram import BotCommand

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, config.log_level.upper(), logging.INFO)
)

# Also log to file as requested in spec
log_dir = os.path.join(get_data_dir(), "logs")
os.makedirs(log_dir, exist_ok=True)
file_handler = logging.FileHandler(os.path.join(log_dir, "bot.log"))
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Zestogram Telegram Bot is running and healthy!")

    def log_message(self, format, *args):
        pass

def start_health_server(port: int):
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Render Health Check HTTP server started on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Could not start HTTP health server on port {port}: {e}")

async def post_init(application):
    from src.cleanup import cleanup_loop
    await init_db()
    await start_workers(application)
    asyncio.create_task(cleanup_loop())
    
    # Register default commands for auto-complete menu (general users)
    commands = [
        BotCommand("start", "Start bot and show menu"),
        BotCommand("help", "Help guide & instructions"),
        BotCommand("mode", "Toggle download / send to admin mode"),
        BotCommand("status", "Check your plan and quota"),
        BotCommand("cancel", "Cancel your pending download jobs"),
        BotCommand("clear", "Clear recent bot messages in chat")
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")
        
    config.validate_payment_config()
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
    app.add_handler(CommandHandler("status", status_command))
    from src.handlers.admin_mode_handler import (
        mode_command,
        mode_toggle_callback,
        admin_submission_decision_callback
    )
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("clear", clear_command))

    from src.handlers.admin import (
        stats_command,
        user_command,
        grantpro_command,
        grantunlimited_command,
        revoke_command,
        addadmin_command,
        removeadmin_command,
        admins_command,
        users_command,
        health_command,
        ban_command,
        unban_command,
        admin_command
    )
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("user", user_command))
    app.add_handler(CommandHandler("grantpro", grantpro_command))
    app.add_handler(CommandHandler("grantunlimited", grantunlimited_command))
    app.add_handler(CommandHandler("revoke", revoke_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("addadmin", addadmin_command))
    app.add_handler(CommandHandler("removeadmin", removeadmin_command))
    app.add_handler(CommandHandler("admins", admins_command))
    
    from telegram.ext import CallbackQueryHandler
    from src.handlers.convert_handler import convert_callback
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^(toggle_auto_cleanup|settings_done)$"))
    app.add_handler(CallbackQueryHandler(convert_callback, pattern="^convert_to:"))
    app.add_handler(CallbackQueryHandler(mode_toggle_callback, pattern="^set_mode:(normal|admin)$"))
    app.add_handler(CallbackQueryHandler(admin_submission_decision_callback, pattern="^(approve_sub|disapprove_sub):"))
    
    app.add_handler(MessageHandler((filters.TEXT | filters.Document.ALL | filters.VIDEO | filters.AUDIO) & ~filters.COMMAND, handle_message))
    
    # Start HTTP server on PORT for Render web service health check & keep-awake cron pinging
    port = int(os.getenv("PORT", config.port or 10000))
    health_thread = threading.Thread(target=start_health_server, args=(port,), daemon=True)
    health_thread.start()

    if config.webhook_url:
        logger.info(f"Starting webhook on {config.webhook_url} (port {config.webhook_port})")
        app.run_webhook(
            listen="0.0.0.0",
            port=config.webhook_port,
            url_path=config.telegram_bot_token,
            webhook_url=f"{config.webhook_url.rstrip('/')}/{config.telegram_bot_token}",
            drop_pending_updates=True
        )
    else:
        logger.info("Bot is starting up in polling mode...")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
