from telegram import Update, InputMediaVideo, InputMediaPhoto
from telegram.ext import ContextTypes
from telegram.error import TelegramError, RetryAfter
import logging
import asyncio
from typing import List, Dict, Any
from src.config import config
from src.db import cache_media, get_cached_media

logger = logging.getLogger(__name__)

async def send_downloaded_media(
    context: ContextTypes.DEFAULT_TYPE, 
    chat_id: int, 
    url: str,
    files: List[Dict[str, Any]]
):
    if not files:
        return

    caption = files[0]['caption'] if config.include_caption else ""
    # Truncate caption to 1024 chars (Telegram limit)
    if len(caption) > 1024:
        caption = caption[:1021] + "..."

    # Check cache for simple deduplication
    cached_file_id = await get_cached_media(url)

    # Retry wrapper for flood control
    async def _send_with_retry(coro):
        retries = 0
        while retries < 3:
            try:
                return await coro
            except RetryAfter as e:
                logger.warning(f"Flood control: Retrying after {e.retry_after} seconds.")
                await asyncio.sleep(e.retry_after)
                retries += 1
            except TelegramError as e:
                raise e
        raise Exception("Max retries exceeded for flood control")

    if len(files) == 1:
        f = files[0]
        path = f['path']
        is_video = f['is_video']
        
        # Try using cached file_id if available
        if cached_file_id:
            try:
                if is_video:
                    await _send_with_retry(context.bot.send_video(chat_id=chat_id, video=cached_file_id, caption=caption))
                else:
                    await _send_with_retry(context.bot.send_photo(chat_id=chat_id, photo=cached_file_id, caption=caption))
                return
            except TelegramError as e:
                logger.warning(f"Failed to send cached media (file_id might be invalid), uploading normally: {e}")
        
        # Standard upload
        with open(path, 'rb') as file_obj:
            if is_video:
                msg = await _send_with_retry(context.bot.send_video(
                    chat_id=chat_id, 
                    video=file_obj, 
                    caption=caption,
                    write_timeout=300,
                    connect_timeout=60,
                    read_timeout=300
                ))
                if msg.video:
                    await cache_media(url, msg.video.file_id)
            else:
                msg = await _send_with_retry(context.bot.send_photo(
                    chat_id=chat_id, 
                    photo=file_obj, 
                    caption=caption
                ))
                if msg.photo:
                    await cache_media(url, msg.photo[-1].file_id)
    else:
        # Carousel / Media Group
        # Telegram allows max 10 items per media group.
        chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
        
        for i, chunk in enumerate(chunks):
            media_group = []
            open_files = []
            
            try:
                for j, f in enumerate(chunk):
                    path = f['path']
                    is_video = f['is_video']
                    file_obj = open(path, 'rb')
                    open_files.append(file_obj)
                    
                    item_caption = caption if (i == 0 and j == 0) else ""
                    
                    if is_video:
                        media_group.append(InputMediaVideo(media=file_obj, caption=item_caption))
                    else:
                        media_group.append(InputMediaPhoto(media=file_obj, caption=item_caption))
                        
                msgs = await _send_with_retry(context.bot.send_media_group(
                    chat_id=chat_id, 
                    media=media_group,
                    write_timeout=300,
                    connect_timeout=60,
                    read_timeout=300
                ))
                
                # Cache the first item's file_id
                if msgs and i == 0:
                    first_msg = msgs[0]
                    if first_msg.video:
                        await cache_media(url, first_msg.video.file_id)
                    elif first_msg.photo:
                        await cache_media(url, first_msg.photo[-1].file_id)
            finally:
                for file_obj in open_files:
                    file_obj.close()
