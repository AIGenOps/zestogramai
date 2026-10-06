import asyncio
import logging
import time
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError
import json
import redis.asyncio as aioredis
from typing import Dict, Any

from src.config import config
from src.db import get_job, update_job_status, get_user_settings
from src.downloader import download_media, cleanup_job_files, DownloadError
from src.utils.media_sender import send_downloaded_media
from src.utils.media_processor import compress_video, strip_metadata

logger = logging.getLogger(__name__)

memory_queue = asyncio.Queue()
use_memory_queue = False
redis_client = None

async def init_redis():
    global redis_client, use_memory_queue
    if use_memory_queue:
        return
    if not config.redis_url:
        use_memory_queue = True
        logger.info("REDIS_URL not configured. Operating with in-memory task queue.")
        return
    if not redis_client:
        try:
            client = aioredis.from_url(config.redis_url, socket_connect_timeout=3)
            await client.ping()
            redis_client = client
            logger.info("Successfully connected to Redis.")
        except Exception as e:
            logger.warning(f"Could not connect to Redis ({e}). Falling back to in-memory queue.")
            use_memory_queue = True

async def pop_job() -> Dict[str, Any]:
    await init_redis()
    if use_memory_queue:
        return await memory_queue.get()
    else:
        raw_data = await redis_client.brpop('zestogram:job_queue', timeout=0)
        if raw_data:
            return json.loads(raw_data[1])
        return None

async def worker(worker_id: int, app: Application):
    await init_redis()
    logger.info(f"Worker {worker_id} started.")
    while True:
        try:
            job_data = await pop_job()
            if not job_data:
                continue
                
            job_id = job_data['job_id']
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
                
                chat_id = job['chat_id']
                url = job['url']
                
                # Fetch user settings for auto-cleanup
                settings = await get_user_settings(user_id)
                
                # Use force_audio flag from job_data (added via /audio command)
                audio_only = job_data.get('force_audio', False)
                auto_cleanup = settings.get('auto_cleanup', False)
                
                last_edit_time = 0
                
                from src.utils.queue_ui import batch_tracker
                is_batch = job_data.get('is_batch', False)
                
                async def notify_ui(status, progress=None):
                    if is_batch:
                        await batch_tracker.update_job(app.bot, message_id, job_id, status, progress)
                    else:
                        emoji = "⬇️" if status == 'downloading' else "⚙️" if status == 'processing' else "📤" if status == 'uploading' else "❌"
                        text = f"{emoji} {status.title()}..."
                        if progress:
                            text += f"\n{progress}"
                        try:
                            await app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
                        except TelegramError:
                            pass
                            
                # This callback will run in a separate thread because yt-dlp is synchronous
                def progress_hook(d):
                    nonlocal last_edit_time
                    if d['status'] == 'downloading':
                        current_time = time.time()
                        if current_time - last_edit_time > 3.0: # Throttle to 3 seconds
                            last_edit_time = current_time
                            try:
                                percent = d.get('_percent_str', '').strip()
                                speed = d.get('_speed_str', '').strip()
                                eta = d.get('_eta_str', '').strip()
                                
                                progress = f"{percent} ({speed}) ETA: {eta}"
                                if is_batch:
                                    progress = percent
                                
                                asyncio.run_coroutine_threadsafe(
                                    notify_ui('downloading', progress),
                                    asyncio.get_running_loop()
                                )
                            except Exception:
                                pass
    
                from src.db import get_cached_media
                cached_file_id = await get_cached_media(url, is_audio=audio_only)
                
                if cached_file_id and not url.startswith("convert:"):
                    logger.info(f"Cache hit for job {job_id} ({url}). Bypassing download.")
                    processed_files = [{
                        'path': 'cached.mp3' if audio_only else 'cached.mp4',
                        'is_video': not audio_only,
                        'caption': ''
                    }]
                    await notify_ui('uploading')
                else:
                    await notify_ui('downloading')
        
                    if url.startswith("convert:"):
                        from src.converter import process_conversion
                        files = await process_conversion(app.bot, job_id, url)
                    else:
                        files = await download_media(job_id, url, audio_only=audio_only, progress_callback=progress_hook)
                    
                    await notify_ui('processing')
        
                    # Post-download processing (Compression & Metadata)
                    processed_files = []
                    for f in files:
                        path = f['path']
                        is_video = f['is_video']
                        
                        # Strip metadata
                        await strip_metadata(path, is_video)
                        
                        # Compress video if local bot API is disabled and >50MB
                        if not config.use_local_bot_api:
                            import os
                            if os.path.exists(path) and os.path.getsize(path) > 49.5 * 1024 * 1024:
                                if is_video:
                                    logger.info(f"Compressing video {path} as it exceeds 50MB...")
                                    compressed_path = path + ".compressed.mp4"
                                    success = await compress_video(path, compressed_path)
                                    if success and os.path.getsize(compressed_path) <= 49.5 * 1024 * 1024:
                                        os.replace(compressed_path, path)
                                    else:
                                        if os.path.exists(compressed_path):
                                            os.remove(compressed_path)
                                        raise DownloadError(f"Video is too large ({os.path.getsize(path)/1024/1024:.1f}MB) and cannot be compressed below 50MB limit.", retryable=False)
                                else:
                                    raise DownloadError(f"File is too large ({os.path.getsize(path)/1024/1024:.1f}MB) for Telegram's 50MB limit.", retryable=False)
                                    
                        processed_files.append(f)
    
                await notify_ui('uploading')
    
                class DummyContext:
                    def __init__(self, bot):
                        self.bot = bot
                
                await send_downloaded_media(DummyContext(app.bot), chat_id, url, processed_files)
                
                await update_job_status(job_id, 'success')
                await notify_ui('success')
                
                if not is_batch:
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
                await notify_ui('failed', str(e))
            except Exception as e:
                logger.exception(f"Unexpected error in job {job_id}: {e}")
                await update_job_status(job_id, 'failed', str(e))
                await notify_ui('failed', 'Unexpected error')
            finally:
                cleanup_job_files(job_id)

        except Exception as e:
            logger.exception(f"Unexpected error in worker loop: {e}")
            await asyncio.sleep(1)

async def start_workers(app: Application):
    await init_redis()
    for i in range(config.max_concurrent_downloads):
        asyncio.create_task(worker(i, app))

async def enqueue_job(job_data: Dict[str, Any]):
    await init_redis()
    if use_memory_queue:
        await memory_queue.put(job_data)
    else:
        await redis_client.lpush('zestogram:job_queue', json.dumps(job_data))

async def get_queue_length() -> int:
    await init_redis()
    if use_memory_queue:
        return memory_queue.qsize()
    else:
        return await redis_client.llen('zestogram:job_queue')

