import os
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

async def compress_video(input_path: str, output_path: str) -> bool:
    """
    Compresses video down to <50MB using a fast CRF or bitrate target if it exceeds limits.
    Returns True if successful.
    """
    try:
        # Simple compression: CRF 28 is usually good enough for mobile viewing.
        # We also scale it down to 720p max to save space.
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "scale='min(720,iw)':-2",
            "-vcodec", "libx264", "-crf", "28",
            "-preset", "fast",
            "-acodec", "aac", "-b:a", "128k",
            output_path
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode == 0 and os.path.exists(output_path):
            return True
        else:
            logger.error(f"Compression failed: {stderr.decode()}")
            return False
    except Exception as e:
        logger.error(f"Error compressing video: {e}")
        return False

async def strip_metadata(file_path: str, is_video: bool) -> bool:
    """
    Strips EXIF and metadata from the file.
    For videos, uses ffmpeg. For images, we can use ffmpeg or Pillow. 
    Here we'll use ffmpeg for both for simplicity and reliability if it works, or fallback.
    """
    ext = os.path.splitext(file_path)[1]
    tmp_path = file_path + ".nometa" + ext
    try:
        if is_video:
            cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-map_metadata", "-1", "-c:v", "copy", "-c:a", "copy",
                tmp_path
            ]
        elif file_path.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg')):
            cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-map_metadata", "-1", "-c:a", "copy",
                tmp_path
            ]
        else:
            # ffmpeg can also strip metadata from images while keeping the format
            cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-map_metadata", "-1", 
                tmp_path
            ]
            
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode == 0 and os.path.exists(tmp_path):
            os.replace(tmp_path, file_path)
            return True
        else:
            logger.warning(f"Failed to strip metadata: {stderr.decode()}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False
    except Exception as e:
        logger.error(f"Error stripping metadata: {e}")
        return False
