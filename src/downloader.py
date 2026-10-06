import yt_dlp
import asyncio
import os
import shutil
import logging
from typing import List, Dict, Any, Optional, Callable
from src.config import config
import instaloader

logger = logging.getLogger(__name__)

class DownloadError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        self.message = message
        self.retryable = retryable
        super().__init__(self.message)

def _download_sync(url: str, output_dir: str, audio_only: bool, progress_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
    # Custom filename (Feature 11)
    outtmpl = os.path.join(output_dir, '%(title)s_%(id)s.%(ext)s')
    
    ydl_opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'noplaylist': False,
        'format_sort': ['vcodec:h264', 'ext:mp4:m4a'],
        'restrictfilenames': True,
    }
    
    if progress_callback:
        ydl_opts['progress_hooks'] = [progress_callback]
        
    if not audio_only and ('youtube.com' in url or 'youtu.be' in url):
        def _check_duration(info, *args, **kwargs):
            duration = info.get('duration', 0)
            if duration and duration > 180:
                raise DownloadError("YouTube videos longer than 3 minutes are only allowed as Audio. Use the /audio command.", retryable=False)
            return None
        ydl_opts['match_filter'] = _check_duration
        
    if audio_only:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    
    if config.cookies_file_path and os.path.exists(config.cookies_file_path):
        ydl_opts['cookiefile'] = config.cookies_file_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if not info:
                raise DownloadError("Failed to extract info from URL.", retryable=True)
            
            caption = info.get('description') or info.get('title') or ''
            
            downloaded_files = []
            for root, _, filenames in os.walk(output_dir):
                for f in filenames:
                    if f.endswith('.part') or f.endswith('.ytdl') or f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        continue
                    ext = f.lower()
                    if audio_only and not ext.endswith(('.mp3', '.m4a', '.wav', '.ogg')):
                        continue
                    downloaded_files.append({
                        'path': os.path.join(root, f),
                        'caption': caption,
                        'is_video': ext.endswith(('.mp4', '.webm', '.mkv', '.mov', '.avi'))
                    })
            
            downloaded_files.sort(key=lambda x: x['path'])
            
            if len(downloaded_files) > 1 and "instagram.com" in url:
                raise DownloadError("Instagram posts and carousels are no longer supported. Please send single video Reels only.", retryable=False)
                
            return downloaded_files
            
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp error: {error_msg}")
        retryable = True
        if any(kw in error_msg.lower() for kw in ("private", "login", "not found", "404", "sign in")):
            retryable = False
        raise DownloadError(f"yt-dlp failed: {error_msg}", retryable=retryable)
    except DownloadError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in yt-dlp download: {e}")
        raise DownloadError(f"Unexpected error: {str(e)}", retryable=False)

def _fallback_instaloader_sync(url: str, output_dir: str) -> List[Dict[str, Any]]:
    L = instaloader.Instaloader(dirname_pattern=output_dir, download_comments=False, save_metadata=False)
    try:
        import re
        match = re.search(r"/(?:p|reel|reels|tv)/([^/?#&]+)", url)
        if not match:
            raise Exception("Could not find shortcode for Instaloader fallback.")
        shortcode = match.group(1)
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=output_dir)
        
        downloaded_files = []
        for root, _, filenames in os.walk(output_dir):
            for f in filenames:
                if f.endswith('.txt') or f.endswith('.json') or f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    continue
                downloaded_files.append({
                    'path': os.path.join(root, f),
                    'caption': post.caption or '',
                    'is_video': f.lower().endswith(('.mp4', '.mov'))
                })
        downloaded_files.sort(key=lambda x: x['path'])
        
        if len(downloaded_files) > 1 and "instagram.com" in url:
            raise DownloadError("Instagram posts and carousels are no longer supported. Please send single video Reels only.", retryable=False)
            
        return downloaded_files
    except Exception as e:
        logger.error(f"Instaloader fallback failed: {e}")
        raise DownloadError(f"Fallback failed: {e}", retryable=False)

from src.config import get_data_dir

async def download_media(job_id: int, url: str, audio_only: bool = False, progress_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
    data_dir = get_data_dir()
    output_dir = os.path.join(data_dir, "tmp", str(job_id))
    os.makedirs(output_dir, exist_ok=True)
    
    retries = 0
    while retries <= config.max_retries:
        try:
            files = await asyncio.to_thread(_download_sync, url, output_dir, audio_only, progress_callback)
            if not files:
                raise DownloadError("No files were downloaded.", retryable=False)
            return files
        except DownloadError as e:
            if e.retryable and retries < config.max_retries:
                retries += 1
                logger.warning(f"Download failed for job {job_id}, retrying ({retries}/{config.max_retries})...")
                await asyncio.sleep(2 * retries)
            elif not e.retryable and "instagram.com" in url:
                logger.info("Attempting Instaloader fallback...")
                try:
                    files = await asyncio.to_thread(_fallback_instaloader_sync, url, output_dir)
                    if files:
                        return files
                except Exception:
                    pass
                shutil.rmtree(output_dir, ignore_errors=True)
                raise e
            else:
                shutil.rmtree(output_dir, ignore_errors=True)
                raise
        except Exception as e:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise e

def cleanup_job_files(job_id: int):
    data_dir = get_data_dir()
    tmp_dir = os.path.join(data_dir, "tmp", str(job_id))
    if not config.keep_files_after_send:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        archive_dir = os.path.join(data_dir, "downloads", str(job_id))
        if os.path.exists(tmp_dir):
            os.makedirs(os.path.dirname(archive_dir), exist_ok=True)
            shutil.move(tmp_dir, archive_dir)
