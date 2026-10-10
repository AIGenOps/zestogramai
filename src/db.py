import aiosqlite
import asyncio
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
import os

from src.config import get_data_dir

def get_db_path() -> str:
    env_path = os.getenv("DB_PATH")
    if env_path:
        return env_path
    return os.path.join(get_data_dir(), "bot.db")

DB_PATH = get_db_path()

async def init_db():
    global DB_PATH
    DB_PATH = get_db_path()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
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
        await db.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                membership_type TEXT DEFAULT 'FREE',
                pro_expires_at TIMESTAMP,
                is_unlimited BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                quota_window_start TIMESTAMP,
                quota_usage INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        from src.config import config
        for admin_id in config.parsed_admin_user_ids:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
        if config.owner_id:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (config.owner_id,))

        await db.execute('''
            CREATE TABLE IF NOT EXISTS admin_mode_users (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'NONE',
                current_mode TEXT NOT NULL DEFAULT 'NORMAL',
                topic_id INTEGER,
                user_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        async with db.execute("PRAGMA table_info(jobs)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if 'quota_recorded' not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN quota_recorded INTEGER DEFAULT 0")
            if 'original_message_id' not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN original_message_id INTEGER")
            if 'target_chat_id' not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN target_chat_id INTEGER")
            if 'message_thread_id' not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN message_thread_id INTEGER")

        
        # Recover stale downloading jobs from previous process crash/restart
        await db.execute(
            "UPDATE jobs SET status = 'failed', error_reason = 'Interrupted by system restart' WHERE status = 'downloading'"
        )
        await db.commit()

async def recover_stale_jobs() -> int:
    """
    Recovers any jobs left in 'downloading' status due to process interruption or crash.
    Transitions them to 'failed' so abandoned pending quota reservations are freed.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE jobs SET status = 'failed', error_reason = 'Interrupted by system restart' WHERE status = 'downloading'"
        )
        await db.commit()
        return cursor.rowcount

async def get_pending_jobs_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status IN ('queued', 'downloading')",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def has_earlier_pending_job(user_id: int, job_id: int) -> bool:
    """
    Returns True if there is an earlier job (id < job_id) for the same user
    that is still in 'queued' or 'downloading' status.
    Guarantees strict same-user FIFO ordering across workers.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM jobs WHERE user_id = ? AND id < ? AND status IN ('queued', 'downloading') LIMIT 1",
            (user_id, job_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def mark_job_quota_recorded(job_id: int) -> bool:
    """
    Atomically marks quota_recorded = 1 for job_id if it was 0.
    Returns True if this was the first time (successful mark), False if already marked.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE jobs SET quota_recorded = 1 WHERE id = ? AND quota_recorded = 0",
            (job_id,)
        )
        await db.commit()
        return cursor.rowcount > 0

async def record_delivery_atomic(
    user_id: int,
    job_id: Optional[int],
    window_hours: int,
    free_limit: int,
    pro_limit: int,
    now: datetime
) -> Tuple[bool, Dict[str, Any]]:
    """
    Atomically records a successful Telegram delivery in a single SQLite transaction:
    1. Checks and marks job_id quota_recorded = 1 if job_id provided.
    2. Calculates new quota window and usage.
    3. Saves user record to DB.
    4. Commits both job and user update in ONE transaction.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        if job_id is not None:
            cursor = await db.execute(
                "UPDATE jobs SET quota_recorded = 1 WHERE id = ? AND quota_recorded = 0",
                (job_id,)
            )
            if cursor.rowcount == 0:
                async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
                    row = await cur.fetchone()
                    return False, dict(row) if row else {}

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            user_row = dict(row) if row else {}

        membership_type = user_row.get('membership_type') or 'FREE'
        pro_expires_at_str = user_row.get('pro_expires_at')
        is_unlimited = bool(user_row.get('is_unlimited'))
        window_start_str = user_row.get('quota_window_start')
        stored_usage = user_row.get('quota_usage', 0)

        def _parse_dt(val):
            if not val:
                return None
            try:
                dt = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None

        pro_expires_at = _parse_dt(pro_expires_at_str)
        window_start = _parse_dt(window_start_str)

        if is_unlimited:
            effective = 'UNLIMITED'
            limit = float('inf')
        elif pro_expires_at is not None and pro_expires_at > now:
            effective = 'PRO'
            limit = float(pro_limit)
        else:
            effective = 'FREE'
            limit = float(free_limit)

        window_duration = timedelta(hours=window_hours)

        if window_start is None or now >= (window_start + window_duration):
            new_window_start = now
            new_usage = 1
            recorded = True
        else:
            new_window_start = window_start
            if limit != float('inf') and stored_usage >= limit:
                new_usage = stored_usage
                recorded = False
            else:
                new_usage = stored_usage + 1
                recorded = True

        new_window_start_str = new_window_start.isoformat() if new_window_start else None

        if recorded:
            await db.execute('''
                INSERT INTO users (user_id, membership_type, pro_expires_at, is_unlimited, quota_window_start, quota_usage, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    membership_type = excluded.membership_type,
                    pro_expires_at = excluded.pro_expires_at,
                    is_unlimited = excluded.is_unlimited,
                    quota_window_start = excluded.quota_window_start,
                    quota_usage = excluded.quota_usage,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, membership_type, pro_expires_at_str, 1 if is_unlimited else 0, new_window_start_str, new_usage))
            
            await db.commit()
            return True, {
                'user_id': user_id,
                'membership_type': membership_type,
                'pro_expires_at': pro_expires_at_str,
                'is_unlimited': is_unlimited,
                'quota_window_start': new_window_start_str,
                'quota_usage': new_usage,
                'effective_plan': effective,
                'limit': limit
            }
        else:
            await db.commit()
            return False, user_row

async def get_user_db(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def save_user_db(
    user_id: int,
    membership_type: str = 'FREE',
    pro_expires_at: Optional[str] = None,
    is_unlimited: bool = False,
    quota_window_start: Optional[str] = None,
    quota_usage: int = 0
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO users (user_id, membership_type, pro_expires_at, is_unlimited, quota_window_start, quota_usage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                membership_type = excluded.membership_type,
                pro_expires_at = excluded.pro_expires_at,
                is_unlimited = excluded.is_unlimited,
                quota_window_start = excluded.quota_window_start,
                quota_usage = excluded.quota_usage,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, membership_type, pro_expires_at, 1 if is_unlimited else 0, quota_window_start, quota_usage))
        await db.commit()

async def add_job(
    user_id: int,
    chat_id: int,
    message_id: int,
    url: str,
    original_message_id: Optional[int] = None,
    target_chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
    status: str = "queued"
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO jobs (user_id, chat_id, message_id, original_message_id, url, target_chat_id, message_thread_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, message_id, original_message_id, url, target_chat_id, message_thread_id, status)
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

async def update_job_message_id(job_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE jobs SET message_id = ? WHERE id = ?", (message_id, job_id))
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

async def cache_media(url: str, file_id: str, is_audio: bool = False):
    cache_key = f"{url}_audio" if is_audio else url
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO cached_media (url, file_id) VALUES (?, ?)", (cache_key, file_id))
        await db.commit()

async def get_cached_media(url: str, is_audio: bool = False) -> Optional[str]:
    cache_key = f"{url}_audio" if is_audio else url
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT file_id FROM cached_media WHERE url = ?", (cache_key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def cancel_user_jobs(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE jobs SET status = 'cancelled', error_reason = 'Cancelled by user', completed_at = CURRENT_TIMESTAMP WHERE user_id = ? AND status IN ('queued', 'pending_approval')",
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

async def is_user_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def ban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_unique_users_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM jobs") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def get_detailed_user_stats() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT 
                user_id, 
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_downloads,
                MAX(created_at) as last_active
            FROM jobs 
            WHERE user_id IS NOT NULL 
            GROUP BY user_id 
            ORDER BY total_requests DESC
        """) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

async def get_all_queued_jobs() -> List[Dict[str, Any]]:
    """
    Returns all jobs with status = 'queued' ordered by id ASC.
    Used during startup queue reconciliation so SQLite remains the authoritative source of truth.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id ASC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

async def add_admin_db(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_admin_db(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0

async def is_admin_db(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def get_all_admins_db() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM admins ORDER BY created_at ASC") as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]

async def get_system_stats() -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM (SELECT user_id FROM users UNION SELECT user_id FROM jobs WHERE user_id IS NOT NULL)") as cur:
            total_users = (await cur.fetchone())[0] or 0
            
        now = datetime.now(timezone.utc)
        
        async with db.execute("SELECT * FROM users") as cur:
            user_rows = [dict(r) for r in await cur.fetchall()]
            
        free_count = 0
        pro_count = 0
        unlim_count = 0
        
        users_in_table = set()
        for u in user_rows:
            uid = u['user_id']
            users_in_table.add(uid)
            is_unlim = bool(u.get('is_unlimited'))
            exp_str = u.get('pro_expires_at')
            
            exp_dt = None
            if exp_str:
                try:
                    exp_dt = datetime.fromisoformat(exp_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
                    
            if is_unlim:
                unlim_count += 1
            elif exp_dt and exp_dt > now:
                pro_count += 1
            else:
                free_count += 1
                
        free_count += max(0, total_users - len(users_in_table))
        
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'success'") as cur:
            succ_total = (await cur.fetchone())[0] or 0
            
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'success' AND completed_at >= datetime('now', '-24 hours')") as cur:
            succ_24h = (await cur.fetchone())[0] or 0
            
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'downloading'") as cur:
            processing = (await cur.fetchone())[0] or 0
            
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'") as cur:
            queued = (await cur.fetchone())[0] or 0
            
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'") as cur:
            failed = (await cur.fetchone())[0] or 0
            
        return {
            'total_users': total_users,
            'free_users': free_count,
            'pro_users': pro_count,
            'unlimited_users': unlim_count,
            'successful_downloads': succ_total,
            'downloads_24h': succ_24h,
            'currently_processing': processing,
            'queued': queued,
            'failed': failed
        }

async def get_admin_mode_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM admin_mode_users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def upsert_admin_mode_user(
    user_id: int,
    status: str = "NONE",
    current_mode: str = "NORMAL",
    topic_id: Optional[int] = None,
    user_name: Optional[str] = None
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO admin_mode_users (user_id, status, current_mode, topic_id, user_name, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                status = excluded.status,
                current_mode = excluded.current_mode,
                topic_id = COALESCE(excluded.topic_id, admin_mode_users.topic_id),
                user_name = COALESCE(excluded.user_name, admin_mode_users.user_name),
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, status, current_mode, topic_id, user_name))
        await db.commit()

async def update_admin_mode_status(
    user_id: int,
    status: str,
    current_mode: Optional[str] = None,
    topic_id: Optional[int] = None,
    clear_topic: bool = False
):
    async with aiosqlite.connect(DB_PATH) as db:
        if clear_topic:
            if current_mode is not None:
                await db.execute(
                    "UPDATE admin_mode_users SET status = ?, current_mode = ?, topic_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (status, current_mode, user_id)
                )
            else:
                await db.execute(
                    "UPDATE admin_mode_users SET status = ?, topic_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (status, user_id)
                )
        elif current_mode is not None and topic_id is not None:
            await db.execute(
                "UPDATE admin_mode_users SET status = ?, current_mode = ?, topic_id = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (status, current_mode, topic_id, user_id)
            )
        elif current_mode is not None:
            await db.execute(
                "UPDATE admin_mode_users SET status = ?, current_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (status, current_mode, user_id)
            )
        elif topic_id is not None:
            await db.execute(
                "UPDATE admin_mode_users SET status = ?, topic_id = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (status, topic_id, user_id)
            )
        else:
            await db.execute(
                "UPDATE admin_mode_users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (status, user_id)
            )
        await db.commit()

async def update_admin_mode_current_mode(user_id: int, current_mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE admin_mode_users SET current_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (current_mode, user_id)
        )
        await db.commit()

async def get_pending_submission_jobs(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND status = 'pending_approval' ORDER BY id ASC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def cancel_pending_submission_jobs(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE jobs SET status = 'cancelled', error_reason = 'Submission request declined', completed_at = CURRENT_TIMESTAMP WHERE user_id = ? AND status = 'pending_approval'",
            (user_id,)
        )
        await db.commit()
        return cursor.rowcount

async def activate_pending_submission_jobs(
    user_id: int,
    target_chat_id: int,
    message_thread_id: int
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE jobs SET status = 'queued', target_chat_id = ?, message_thread_id = ? WHERE user_id = ? AND status = 'pending_approval'",
            (target_chat_id, message_thread_id, user_id)
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND status = 'queued' AND target_chat_id = ? AND message_thread_id = ? ORDER BY id ASC",
            (user_id, target_chat_id, message_thread_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


