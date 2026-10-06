import asyncio
import logging
from typing import Dict, List
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.error import TelegramError
import re

logger = logging.getLogger(__name__)

class BatchTracker:
    def __init__(self):
        self.batches: Dict[int, Dict] = {} # key: message_id
        self.lock = asyncio.Lock()
        
    def _get_platform_title(self, url: str) -> str:
        if "instagram.com" in url or "instagr.am" in url:
            return "📸 Instagram"
        elif "youtube.com" in url or "youtu.be" in url or "y2u.be" in url:
            return "🎥 YouTube"
        return "🔗 Link"
        
    def add_batch(self, chat_id: int, message_id: int, jobs_info: List[Dict]):
        # jobs_info: [{'job_id': 1, 'url': '...'}, ...]
        for j in jobs_info:
            j['title'] = self._get_platform_title(j['url'])
            
        self.batches[message_id] = {
            'chat_id': chat_id,
            'jobs': {j['job_id']: {**j, 'status': 'queued'} for j in jobs_info},
            'dirty': False,
            'task': None
        }

    async def update_job(self, bot: Bot, message_id: int, job_id: int, status: str, progress_text: str = None):
        async with self.lock:
            if message_id not in self.batches:
                return
            batch = self.batches[message_id]
            if job_id in batch['jobs']:
                batch['jobs'][job_id]['status'] = status
                if progress_text:
                    batch['jobs'][job_id]['progress'] = progress_text
                batch['dirty'] = True
                
                if batch['task'] is None or batch['task'].done():
                    batch['task'] = asyncio.create_task(self._debounced_update(bot, message_id))

    async def _debounced_update(self, bot: Bot, message_id: int):
        await asyncio.sleep(0.5)
        async with self.lock:
            if message_id not in self.batches:
                return
            batch = self.batches[message_id]
            if not batch['dirty']:
                return
            batch['dirty'] = False
            
            keyboard = []
            all_done = True
            active_count = 0
            
            for j_id, j_data in batch['jobs'].items():
                if j_data['status'] not in ['success', 'failed', 'cancelled']:
                    all_done = False
                    
                if j_data['status'] in ['success', 'failed', 'cancelled']:
                    continue
                    
                active_count += 1
                
                status_emoji = "⏳" if j_data['status'] == 'queued' else "⬇️"
                if j_data['status'] == 'processing':
                    status_emoji = "⚙️"
                elif j_data['status'] == 'uploading':
                    status_emoji = "📤"
                
                title = j_data.get('title', 'Link')
                prog = j_data.get('progress', '')
                if prog:
                    text = f"{status_emoji} {title} - {prog}"
                else:
                    text = f"{status_emoji} {title}"
                    
                keyboard.append([InlineKeyboardButton(text, callback_data="ignore")])
            
            chat_id = batch['chat_id']
            
            if all_done or not keyboard:
                failed_jobs = [j for j in batch['jobs'].values() if j['status'] == 'failed']
                if failed_jobs:
                    error_text = "❌ Some downloads failed:\n"
                    for j in failed_jobs:
                        title = j.get('title', 'Link')
                        prog = j.get('progress', 'Unknown error')
                        error_text += f"- {title}: {prog}\n"
                    
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=error_text[:4000],
                            reply_markup=None
                        )
                    except TelegramError:
                        pass
                else:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=message_id)
                    except TelegramError:
                        pass
                del self.batches[message_id]
            else:
                try:
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"**Processing {active_count} item(s)...**",
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                except TelegramError as e:
                    if "Message is not modified" not in str(e):
                        logger.error(f"Failed to update batch UI: {e}")

batch_tracker = BatchTracker()
