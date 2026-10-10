import asyncio
import logging
import time
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError
import json
import redis.asyncio as aioredis
from typing import Dict, Any, Optional

from src.config import config
from src.db import get_job, update_job_status, get_user_settings
from src.downloader import download_media, cleanup_job_files, DownloadError
from src.utils.media_sender import send_downloaded_media
from src.utils.media_processor import compress_video, strip_metadata

logger = logging.getLogger(__name__)

memory_queues = {
    'unlimited': asyncio.Queue(),
    'pro': asyncio.Queue(),
    'free': asyncio.Queue()
}
use_memory_queue = False
redis_client = None
_pop_counter = 0

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

async def pop_job() -> Optional[Dict[str, Any]]:
    global _pop_counter
    await init_redis()
    
    cycle = _pop_counter % 9
    _pop_counter += 1
    
    # 5:3:1 Weighted Fair Priority ratio (Unlimited : Pro : Free)
    if cycle < 5:
        tier_order = ['unlimited', 'pro', 'free']
    elif cycle < 8:
        tier_order = ['pro', 'unlimited', 'free']
    else:
        tier_order = ['free', 'unlimited', 'pro']

    if use_memory_queue:
        for tier in tier_order:
            q = memory_queues[tier]
            if not q.empty():
                try:
                    return q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
        return None
    else:
        for tier in tier_order:
            raw_data = await redis_client.rpop(f'zestogram:queue:{tier}')
            if raw_data:
                return json.loads(raw_data)
        return None

def map_error_to_user_message(error: Any) -> str:
    err_str = str(error).lower()
    if any(kw in err_str for kw in ("not supported", "unsupported", "posts and carousels")):
        return "This link is not supported."
    if any(kw in err_str for kw in ("private", "login", "not found", "404", "sign in", "unavailable", "removed")):
        return "This video is unavailable or private."
    if any(kw in err_str for kw in ("429", "too many requests", "rate limit", "timeout", "connection", "network", "temporary")):
        return "Unable to access this video right now. Please try again later."
    if any(kw in err_str for kw in ("ffmpeg", "compress", "strip_metadata", "too large", "cannot be compressed", "processing")):
        return "Unable to process this video. Please try again."
    return "Unable to download this video right now. Please try again later."

async def worker(worker_id: int, app: Application):
    await init_redis()
    logger.info(f"Worker {worker_id} started.")
    while True:
        try:
            job_data = await pop_job()
            if not job_data:
                await asyncio.sleep(0.1)
                continue
                
            job_id = job_data['job_id']
            chat_id = job_data['chat_id']
            url = job_data['url']
            message_id = job_data.get('message_id')
            user_id = job_data['user_id']
            original_message_id = job_data.get('original_message_id')
            
            try:
                job = await get_job(job_id)
                if not job or job['status'] in ('cancelled', 'failed', 'success'):
                    continue

                target_chat_id = job.get('target_chat_id') or job_data.get('target_chat_id')
                message_thread_id = job.get('message_thread_id') or job_data.get('message_thread_id')
                is_admin_submission = bool(target_chat_id and message_thread_id)

                # Same-User FIFO Enforcement: Ensure earlier jobs for this user complete first
                from src.db import has_earlier_pending_job
                if await has_earlier_pending_job(user_id, job_id):
                    await enqueue_job(job_data)
                    await asyncio.sleep(0.2)
                    continue

                if not is_admin_submission:
                    from src.services.membership import get_user_quota_info, Plan
                    quota_info = await get_user_quota_info(user_id)
                    if quota_info.effective_plan != Plan.UNLIMITED and quota_info.usage >= quota_info.limit:
                        logger.warning(f"Job {job_id} skipped: User {user_id} quota exhausted.")
                        await update_job_status(job_id, 'failed', 'Quota limit reached')
                        if message_id:
                            try:
                                await app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Quota limit reached for your current plan window.")
                            except TelegramError:
                                pass
                        continue

                await update_job_status(job_id, 'downloading')
                
                chat_id = job['chat_id']
                url = job['url']
                
                audio_only = job_data.get('force_audio', False)
                
                from src.db import get_cached_media
                cached_file_id = await get_cached_media(url, is_audio=audio_only)
                
                if cached_file_id and not url.startswith("convert:"):
                    logger.info(f"Cache hit for job {job_id} ({url}). Bypassing download.")
                    processed_files = [{
                        'path': 'cached.mp3' if audio_only else 'cached.mp4',
                        'is_video': not audio_only,
                        'caption': ''
                    }]
                else:
                    if url.startswith("convert:"):
                        from src.converter import process_conversion
                        files = await process_conversion(app.bot, job_id, url)
                    else:
                        files = await download_media(job_id, url, audio_only=audio_only)
                    
                    processed_files = []
                    for f in files:
                        path = f['path']
                        is_video = f['is_video']
                        
                        await strip_metadata(path, is_video)
                        
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
    
                class DummyContext:
                    def __init__(self, bot):
                        self.bot = bot
                
                # 1. Deliver video to Telegram (user DM or admin topic)
                delivery_chat_id = target_chat_id if is_admin_submission else chat_id
                await send_downloaded_media(
                    DummyContext(app.bot), 
                    delivery_chat_id, 
                    url, 
                    processed_files,
                    message_thread_id=message_thread_id if is_admin_submission else None
                )
                
                # 2. Record successful delivery for quota atomically (only for normal user downloads)
                if not is_admin_submission:
                    from src.services.membership import record_successful_delivery
                    await record_successful_delivery(user_id, job_id=job_id)

                # 3. Mark job as success in DB
                await update_job_status(job_id, 'success')
                
                # 4. Cleanup & DM updates
                if is_admin_submission:
                    if message_id and message_id > 0:
                        try:
                            await app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="✅ Reel sent to admin topic!")
                        except TelegramError:
                            pass
                    if original_message_id and original_message_id > 0:
                        try:
                            await app.bot.delete_message(chat_id=chat_id, message_id=original_message_id)
                        except TelegramError:
                            pass
                else:
                    if message_id and message_id > 0:
                        try:
                            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                        except TelegramError:
                            pass
                    if original_message_id and original_message_id > 0:
                        try:
                            await app.bot.delete_message(chat_id=chat_id, message_id=original_message_id)
                        except TelegramError:
                            pass
    
            except Exception as e:
                mapped_msg = map_error_to_user_message(e)
                logger.error(f"Job {job_id} failed: {e}")
                await update_job_status(job_id, 'failed', str(e))
                if message_id and message_id > 0:
                    try:
                        await app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=mapped_msg)
                    except TelegramError:
                        try:
                            await app.bot.send_message(chat_id=chat_id, text=mapped_msg)
                        except TelegramError:
                            pass
                else:
                    try:
                        await app.bot.send_message(chat_id=chat_id, text=mapped_msg)
                    except TelegramError:
                        pass
            finally:
                cleanup_job_files(job_id)

        except Exception as e:
            logger.exception(f"Unexpected error in worker loop: {e}")
            await asyncio.sleep(1)

async def reconcile_queue():
    """
    Reconciles queued jobs from SQLite database into queue memory/Redis upon startup.
    Ensures SQLite remains authoritative source of truth.
    """
    from src.db import get_all_queued_jobs, recover_stale_jobs
    await recover_stale_jobs()
    queued_jobs = await get_all_queued_jobs()
    for job in queued_jobs:
        job_data = {
            'job_id': job['id'],
            'user_id': job['user_id'],
            'chat_id': job['chat_id'],
            'url': job['url'],
            'message_id': job['message_id'],
            'original_message_id': job.get('original_message_id'),
            'target_chat_id': job.get('target_chat_id'),
            'message_thread_id': job.get('message_thread_id'),
            'is_batch': False
        }
        await enqueue_job(job_data)

async def start_workers(app: Application):
    await init_redis()
    await reconcile_queue()
    for i in range(config.max_concurrent_downloads):
        asyncio.create_task(worker(i, app))


async def enqueue_job(job_data: Dict[str, Any]):
    await init_redis()
    user_id = job_data['user_id']
    if job_data.get('target_chat_id') and job_data.get('message_thread_id'):
        tier = 'unlimited'
    else:
        from src.services.membership import get_effective_plan, Plan
        plan = await get_effective_plan(user_id)
        if plan == Plan.UNLIMITED:
            tier = 'unlimited'
        elif plan == Plan.PRO:
            tier = 'pro'
        else:
            tier = 'free'
        
    job_data['tier'] = tier
    
    if use_memory_queue:
        await memory_queues[tier].put(job_data)
    else:
        await redis_client.lpush(f'zestogram:queue:{tier}', json.dumps(job_data))

async def get_queue_length() -> int:
    await init_redis()
    if use_memory_queue:
        return sum(q.qsize() for q in memory_queues.values())
    else:
        total = 0
        for tier in ('unlimited', 'pro', 'free'):
            total += await redis_client.llen(f'zestogram:queue:{tier}')
        return total

