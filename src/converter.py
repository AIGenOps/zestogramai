import os
import asyncio
import logging
from telegram import Bot
from src.downloader import DownloadError

logger = logging.getLogger(__name__)

async def process_conversion(bot: Bot, job_id: int, url: str) -> list:
    """
    url format: convert:<target_format>:<file_id>
    """
    parts = url.split(":")
    if len(parts) != 3:
        raise DownloadError("Invalid conversion job format.", retryable=False)
        
    _, target_format, file_id = parts
    
    from src.config import get_data_dir
    tmp_dir = os.path.join(get_data_dir(), "tmp", str(job_id))
    os.makedirs(tmp_dir, exist_ok=True)
    
    try:
        tg_file = await bot.get_file(file_id)
        
        original_ext = os.path.splitext(tg_file.file_path)[1]
        if not original_ext:
            original_ext = ".tmp"
            
        input_path = os.path.join(tmp_dir, f"input{original_ext}")
        await tg_file.download_to_drive(input_path)
        
        output_path = os.path.join(tmp_dir, f"converted.{target_format}")
        
        if target_format == "mp4":
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                output_path
            ]
            is_video = True
        else:
            raise DownloadError(f"Unsupported conversion format: {target_format}. Only MP4 video is supported.", retryable=False)
            
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"FFmpeg conversion failed: {stderr.decode()}")
            raise DownloadError("Failed to convert media file.", retryable=False)
            
        if not os.path.exists(output_path):
            raise DownloadError("Conversion produced no output file.", retryable=False)
            
        return [{
            'path': output_path,
            'is_video': is_video
        }]
        
    except Exception as e:
        logger.error(f"Error in process_conversion: {e}")
        raise DownloadError(str(e), retryable=False)
