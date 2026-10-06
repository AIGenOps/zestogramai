import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any

from src.config import config
from src.db import (
    get_user_db,
    save_user_db,
    is_user_banned,
    get_pending_jobs_count,
    mark_job_quota_recorded,
    record_delivery_atomic
)

logger = logging.getLogger(__name__)

class Plan:
    FREE = "FREE"
    PRO = "PRO"
    UNLIMITED = "UNLIMITED"

@dataclass
class UserMembership:
    user_id: int
    membership_type: str        # Nominal stored plan ('FREE', 'PRO', 'UNLIMITED')
    pro_expires_at: Optional[datetime]
    is_unlimited: bool
    effective_plan: str         # Evaluated plan ('FREE', 'PRO', 'UNLIMITED')
    quota_limit: float          # 100, 500, or float('inf')

@dataclass
class QuotaInfo:
    user_id: int
    effective_plan: str
    limit: float                # 100, 500, or float('inf')
    usage: int                  # Active window usage count
    remaining: float            # Remaining limit in current window
    window_start: Optional[datetime]
    window_reset_at: Optional[datetime]
    is_window_active: bool
    pending_jobs: int = 0
    success: bool = True

# Per-user concurrency locks for task and thread safety
_user_locks: Dict[int, asyncio.Lock] = {}
_global_lock = asyncio.Lock()

async def _get_user_lock(user_id: int) -> asyncio.Lock:
    async with _global_lock:
        if user_id not in _user_locks:
            _user_locks[user_id] = asyncio.Lock()
        return _user_locks[user_id]

def _parse_datetime(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            try:
                dt = datetime.strptime(val.split('.')[0], "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
    return None

def _format_datetime(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def get_quota_limit(plan_name: str) -> float:
    if plan_name == Plan.UNLIMITED:
        return float('inf')
    elif plan_name == Plan.PRO:
        return float(config.pro_limit)
    else:
        return float(config.free_limit)

def calculate_effective_plan(
    is_unlimited: bool,
    pro_expires_at: Optional[datetime],
    now: datetime
) -> str:
    if is_unlimited:
        return Plan.UNLIMITED
    if pro_expires_at is not None and pro_expires_at > now:
        return Plan.PRO
    return Plan.FREE

async def get_user_membership(user_id: int, now: Optional[datetime] = None) -> UserMembership:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    user_row = await get_user_db(user_id)
    if not user_row:
        membership_type = Plan.FREE
        pro_expires_at = None
        is_unlimited = False
    else:
        membership_type = user_row.get('membership_type') or Plan.FREE
        pro_expires_at = _parse_datetime(user_row.get('pro_expires_at'))
        is_unlimited = bool(user_row.get('is_unlimited'))

    effective = calculate_effective_plan(is_unlimited, pro_expires_at, now)
    limit = get_quota_limit(effective)

    return UserMembership(
        user_id=user_id,
        membership_type=membership_type,
        pro_expires_at=pro_expires_at,
        is_unlimited=is_unlimited,
        effective_plan=effective,
        quota_limit=limit
    )

async def get_effective_plan(user_id: int, now: Optional[datetime] = None) -> str:
    mem = await get_user_membership(user_id, now=now)
    return mem.effective_plan

async def get_user_quota_info(user_id: int, now: Optional[datetime] = None) -> QuotaInfo:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    mem = await get_user_membership(user_id, now=now)
    user_row = await get_user_db(user_id)

    window_start = _parse_datetime(user_row.get('quota_window_start')) if user_row else None
    stored_usage = user_row.get('quota_usage', 0) if user_row else 0

    window_duration = timedelta(hours=config.quota_window_hours)

    if mem.effective_plan == Plan.UNLIMITED:
        is_active = False
        usage = stored_usage
        window_reset_at = None
    elif window_start is None:
        is_active = False
        usage = 0
        window_reset_at = None
    else:
        window_end = window_start + window_duration
        if now < window_end:
            is_active = True
            usage = stored_usage
            window_reset_at = window_end
        else:
            is_active = False
            usage = 0
            window_reset_at = None


    pending_jobs = await get_pending_jobs_count(user_id)

    if mem.quota_limit == float('inf'):
        remaining = float('inf')
    else:
        remaining = max(0.0, mem.quota_limit - (usage + pending_jobs))

    return QuotaInfo(
        user_id=user_id,
        effective_plan=mem.effective_plan,
        limit=mem.quota_limit,
        usage=usage,
        remaining=remaining,
        window_start=window_start,
        window_reset_at=window_reset_at,
        is_window_active=is_active,
        pending_jobs=pending_jobs
    )

async def _can_user_queue_job_unlocked(user_id: int) -> Tuple[bool, str]:
    if await is_user_banned(user_id):
        return False, "User is banned"
        
    pending_count = await get_pending_jobs_count(user_id)
    if pending_count >= 10:
        return False, "Your queue is full. Please wait for some videos to finish."
    return True, "OK"

async def _can_user_download_unlocked(user_id: int, now: datetime) -> Tuple[bool, str]:
    if await is_user_banned(user_id):
        return False, "User is banned"

    quota = await get_user_quota_info(user_id, now=now)
    if quota.effective_plan == Plan.UNLIMITED:
        return True, "OK"

    pending_count = await get_pending_jobs_count(user_id)
    committed = quota.usage + pending_count

    if committed < quota.limit:
        return True, "OK"
    else:
        return False, f"Quota limit ({int(quota.limit)}) reached for current 24-hour window ({quota.usage} delivered, {pending_count} pending)."

async def can_user_queue_job(user_id: int) -> Tuple[bool, str]:
    """
    Checks per-user queue capacity limit (maximum 10 pending jobs in 'queued' or 'downloading' status).
    """
    lock = await _get_user_lock(user_id)
    async with lock:
        return await _can_user_queue_job_unlocked(user_id)

async def can_user_download(user_id: int, now: Optional[datetime] = None) -> Tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        return await _can_user_download_unlocked(user_id, now)

async def check_user_can_submit(user_id: int, now: Optional[datetime] = None) -> Tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        can_queue, queue_msg = await _can_user_queue_job_unlocked(user_id)
        if not can_queue:
            return False, queue_msg
        return await _can_user_download_unlocked(user_id, now)

async def submit_job_if_allowed(
    user_id: int,
    chat_id: int,
    message_id: int,
    url: str,
    original_message_id: Optional[int] = None,
    now: Optional[datetime] = None
) -> Tuple[bool, str, Optional[int]]:
    """
    Atomically checks per-user queue capacity AND quota limit under the user lock,
    and inserts the job into SQLite if both pass.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    from src.db import add_job
    lock = await _get_user_lock(user_id)
    async with lock:
        can_queue, queue_msg = await _can_user_queue_job_unlocked(user_id)
        if not can_queue:
            return False, queue_msg, None
        can_dl, dl_msg = await _can_user_download_unlocked(user_id, now)
        if not can_dl:
            return False, dl_msg, None
        job_id = await add_job(user_id, chat_id, message_id, url, original_message_id=original_message_id)
        return True, "OK", job_id


async def record_successful_delivery(
    user_id: int,
    job_id: Optional[int] = None,
    now: Optional[datetime] = None
) -> QuotaInfo:
    """
    Atomically records a successful Telegram delivery for the user in a single SQLite transaction.
    Guarantees idempotency via job_id so a job cannot consume quota twice.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        success, _ = await record_delivery_atomic(
            user_id=user_id,
            job_id=job_id,
            window_hours=config.quota_window_hours,
            free_limit=config.free_limit,
            pro_limit=config.pro_limit,
            now=now
        )
        quota_info = await get_user_quota_info(user_id, now=now)
        quota_info.success = success
        return quota_info

async def grant_pro(user_id: int, days: int = 30, now: Optional[datetime] = None) -> UserMembership:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        user_row = await get_user_db(user_id)
        if not user_row:
            is_unlimited = False
            existing_expiry = None
            window_start = None
            quota_usage = 0
        else:
            is_unlimited = bool(user_row.get('is_unlimited'))
            existing_expiry = _parse_datetime(user_row.get('pro_expires_at'))
            window_start = _parse_datetime(user_row.get('quota_window_start'))
            quota_usage = user_row.get('quota_usage', 0)

        duration = timedelta(days=days)

        if existing_expiry is not None and existing_expiry > now:
            new_expiry = existing_expiry + duration
        else:
            new_expiry = now + duration

        membership_type = Plan.PRO if not is_unlimited else Plan.UNLIMITED

        await save_user_db(
            user_id=user_id,
            membership_type=membership_type,
            pro_expires_at=_format_datetime(new_expiry),
            is_unlimited=is_unlimited,
            quota_window_start=_format_datetime(window_start),
            quota_usage=quota_usage
        )

        return await get_user_membership(user_id, now=now)

async def grant_unlimited(user_id: int, now: Optional[datetime] = None) -> UserMembership:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        user_row = await get_user_db(user_id)
        window_start = _parse_datetime(user_row.get('quota_window_start')) if user_row else None
        quota_usage = user_row.get('quota_usage', 0) if user_row else 0

        await save_user_db(
            user_id=user_id,
            membership_type=Plan.UNLIMITED,
            pro_expires_at=None,
            is_unlimited=True,
            quota_window_start=_format_datetime(window_start),
            quota_usage=quota_usage
        )

        return await get_user_membership(user_id, now=now)

async def revoke_membership(user_id: int, now: Optional[datetime] = None) -> UserMembership:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    lock = await _get_user_lock(user_id)
    async with lock:
        user_row = await get_user_db(user_id)
        window_start = _parse_datetime(user_row.get('quota_window_start')) if user_row else None
        quota_usage = user_row.get('quota_usage', 0) if user_row else 0

        await save_user_db(
            user_id=user_id,
            membership_type=Plan.FREE,
            pro_expires_at=None,
            is_unlimited=False,
            quota_window_start=_format_datetime(window_start),
            quota_usage=quota_usage
        )

        return await get_user_membership(user_id, now=now)
