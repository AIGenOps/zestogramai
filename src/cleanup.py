import os
import time
import asyncio
import logging
import aiosqlite
from src.db import DB_PATH

logger = logging.getLogger(__name__)

async def cleanup_loop():
    logger.info("Cleanup loop started.")
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM cached_media WHERE created_at < datetime('now', '-7 days')")
                await db.commit()
            
            now = time.time()
            cutoff = now - (7 * 24 * 60 * 60)
            
            from src.config import get_data_dir
            data_dir = get_data_dir()
            for folder in [os.path.join(data_dir, "tmp"), os.path.join(data_dir, "downloads")]:
                if os.path.exists(folder):
                    for root, dirs, files in os.walk(folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            try:
                                mtime = os.path.getmtime(file_path)
                                if mtime < cutoff:
                                    os.remove(file_path)
                                    logger.info(f"Auto-pruned old file: {file_path}")
                            except Exception as e:
                                logger.error(f"Failed to prune file {file_path}: {e}")
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")
            
        await asyncio.sleep(86400) # Sleep for 24 hours
