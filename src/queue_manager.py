import asyncio
import logging
import time
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError
from typing import Dict, Any

from src.config import config
from src.db import get_job, update_job_status, get_user_settings
from src.downloader import download_media, cleanup_job_files, DownloadError
from src.utils.media_sender import send_downloaded_media
from src.utils.media_processor import compress_video, strip_metadata

logger = logging.getLogger(__name__)

job_queue: asyncio.Queue = asyncio.Queue()

async def worker(worker_id: int, app: Application):
    logger.info(f"Worker {worker_id} started.")
    while True:
        job_data = await job_queue.get()
        job_id = job_data['job_id']
        chat_id = job_data['chat_id']
        url = job_data['url']
        message_id = job_data['message_id']
        user_id = job_data['user_id']
        original_message_id = job_data['original_message_id']
        
        try:
            job = await get_job(job_id)
            if job and job['status'] == 'cancelled':
                continue

            await update_job_status(job_id, 'downloading')
            
            # Fetch user settings
            settings = await get_user_settings(user_id)
            audio_only = settings.get('audio_only', False)
            auto_cleanup = settings.get('auto_cleanup', False)
            
            last_edit_time = 0
            
            # This callback will run in a separate thread because yt-dlp is synchronous
            # We must use call_soon_threadsafe or create_task in the main loop to edit messages
            def progress_hook(d):
                nonlocal last_edit_time
                if d['status'] == 'downloading':
                    current_time = time.time()
                    if current_time - last_edit_time > 3.0: # Throttle to 3 seconds
                        last_edit_time = current_time
                        try:
                            # Parse progress
                            percent = d.get('_percent_str', '').strip()
                            speed = d.get('_speed_str', '').strip()
                            eta = d.get('_eta_str', '').strip()
                            
                            text = f"⬇️ Downloading... {percent}\nSpeed: {speed}\nETA: {eta}"
                            
                            # Schedule the async edit in the main event loop
                            asyncio.run_coroutine_threadsafe(
                                app.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    text=text
                                ),
                                asyncio.get_running_loop()
                            )
                        except Exception:
                            pass

            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="⬇️ Downloading..."
                )
            except TelegramError:
                pass

            files = await download_media(job_id, url, audio_only=audio_only, progress_callback=progress_hook)
            
            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="⚙️ Processing media..."
                )
            except TelegramError:
                pass

            # Post-download processing (Compression & Metadata)
            processed_files = []
            for f in files:
                path = f['path']
                is_video = f['is_video']
                
                # Strip metadata
                await strip_metadata(path, is_video)
                
                # Compress video if local bot API is disabled and >50MB
                if is_video and not config.use_local_bot_api:
                    import os
                    if os.path.exists(path) and os.path.getsize(path) > 50 * 1024 * 1024:
                        logger.info(f"Compressing video {path} as it exceeds 50MB...")
                        compressed_path = path + ".compressed.mp4"
                        success = await compress_video(path, compressed_path)
                        if success:
                            os.replace(compressed_path, path)
                            
                processed_files.append(f)

            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="📤 Uploading to Telegram..."
                )
            except TelegramError:
                pass

            class DummyContext:
                def __init__(self, bot):
                    self.bot = bot
            
            await send_downloaded_media(DummyContext(app.bot), chat_id, url, processed_files)
            
            await update_job_status(job_id, 'success')
            
            try:
                await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramError:
                pass
                
            # Auto-Cleanup: Delete the original message containing the link if setting is on
            if auto_cleanup and original_message_id:
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=original_message_id)
                except TelegramError as e:
                    logger.warning(f"Could not delete original message for auto-cleanup: {e}")

        except DownloadError as e:
            logger.error(f"Job {job_id} failed: {e}")
            await update_job_status(job_id, 'failed', str(e))
            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ Failed to download:\n{e.message}"
                )
            except TelegramError:
                pass
        except Exception as e:
            logger.exception(f"Unexpected error in job {job_id}: {e}")
            await update_job_status(job_id, 'failed', str(e))
            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ An unexpected error occurred."
                )
            except TelegramError:
                pass
        finally:
            cleanup_job_files(job_id)
            job_queue.task_done()

async def start_workers(app: Application):
    for i in range(config.max_concurrent_downloads):
        asyncio.create_task(worker(i, app))

def enqueue_job(job_data: Dict[str, Any]):
    job_queue.put_nowait(job_data)
