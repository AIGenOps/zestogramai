import aiosqlite
import asyncio
from typing import Optional, List, Dict, Any
import os

# Can be overridden for tests
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

async def init_db():
    # Ensure directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                url TEXT,
                status TEXT, -- queued, downloading, success, failed, cancelled
                error_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cached_media (
                url TEXT PRIMARY KEY,
                file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS rate_limits (
                user_id INTEGER,
                action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                audio_only BOOLEAN DEFAULT 0,
                auto_cleanup BOOLEAN DEFAULT 0
            )
        ''')
        await db.commit()

async def add_job(user_id: int, chat_id: int, message_id: int, url: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO jobs (user_id, chat_id, message_id, url, status) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, message_id, url, "queued")
        )
        await db.commit()
        return cursor.lastrowid

async def update_job_status(job_id: int, status: str, error_reason: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status in ("success", "failed", "cancelled"):
            await db.execute(
                "UPDATE jobs SET status = ?, error_reason = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error_reason, job_id)
            )
        else:
            await db.execute(
                "UPDATE jobs SET status = ?, error_reason = ? WHERE id = ?",
                (status, error_reason, job_id)
            )
        await db.commit()

async def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def record_rate_limit(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO rate_limits (user_id) VALUES (?)", (user_id,))
        # clean up old limits
        await db.execute("DELETE FROM rate_limits WHERE action_time < datetime('now', '-1 minute')")
        await db.commit()

async def check_rate_limit(user_id: int, max_per_minute: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE user_id = ? AND action_time >= datetime('now', '-1 minute')",
            (user_id,)
        ) as cursor:
            count = (await cursor.fetchone())[0]
            return count < max_per_minute

async def cache_media(url: str, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO cached_media (url, file_id) VALUES (?, ?)", (url, file_id))
        await db.commit()

async def get_cached_media(url: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT file_id FROM cached_media WHERE url = ?", (url,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def cancel_user_jobs(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE jobs SET status = 'cancelled', error_reason = 'Cancelled by user' WHERE user_id = ? AND status = 'queued'",
            (user_id,)
        )
        await db.commit()
        return cursor.rowcount

async def get_stats() -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        stats = {}
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'success'") as cur:
            stats["total_success"] = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'") as cur:
            stats["total_failed"] = (await cur.fetchone())[0]
        return stats

async def get_user_settings(user_id: int) -> Dict[str, bool]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT audio_only, auto_cleanup FROM user_settings WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return {"audio_only": bool(row[0]), "auto_cleanup": bool(row[1])}
            return {"audio_only": False, "auto_cleanup": False}

async def update_user_setting(user_id: int, setting_key: str, value: bool):
    if setting_key not in ("audio_only", "auto_cleanup"):
        raise ValueError("Invalid setting key")
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Insert default if not exists
        await db.execute(
            "INSERT OR IGNORE INTO user_settings (user_id, audio_only, auto_cleanup) VALUES (?, 0, 0)",
            (user_id,)
        )
        await db.execute(
            f"UPDATE user_settings SET {setting_key} = ? WHERE user_id = ?",
            (1 if value else 0, user_id)
        )
        await db.commit()

