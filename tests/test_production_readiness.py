import os
import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import src.db as db_module
from src.config import config
from src.db import (
    init_db,
    add_job,
    update_job_status,
    get_job,
    get_pending_jobs_count,
    recover_stale_jobs,
    get_all_queued_jobs,
    record_delivery_atomic,
    add_admin_db,
    is_admin_db
)
from src.services.membership import record_successful_delivery, get_user_quota_info, Plan
from src.queue_manager import reconcile_queue, memory_queues, get_queue_length
from src.utils.media_processor import compress_video, strip_metadata
from src.downloader import DownloadError

TEST_USER_ID = 777111

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_prod_readiness.db")
    monkeypatch.setenv("DB_PATH", test_db)
    monkeypatch.setattr(config, "allowed_user_ids", "")
    monkeypatch.setattr(config, "allowed_chat_ids", "")
    await init_db()
    return test_db

@pytest.mark.asyncio
async def test_stale_job_recovery_on_restart():
    # Insert a job stuck in 'downloading' status (e.g. from crash during download)
    j_id = await add_job(TEST_USER_ID, TEST_USER_ID, 1, "https://www.instagram.com/reel/C12345678/")
    await update_job_status(j_id, "downloading")

    # Verify pending job count before recovery is 1
    assert await get_pending_jobs_count(TEST_USER_ID) == 1

    # Run recover_stale_jobs
    recovered_count = await recover_stale_jobs()
    assert recovered_count == 1

    # Verify job status was transitioned to 'failed' and error_reason set
    job = await get_job(j_id)
    assert job["status"] == "failed"
    assert "Interrupted by system restart" in job["error_reason"]

    # Pending capacity is now 0 (freed)
    assert await get_pending_jobs_count(TEST_USER_ID) == 0

@pytest.mark.asyncio
async def test_queue_reconciliation_from_sqlite():
    # Insert queued job directly into SQLite
    j1 = await add_job(TEST_USER_ID, TEST_USER_ID, 10, "https://www.instagram.com/reel/C1001/")
    j2 = await add_job(TEST_USER_ID, TEST_USER_ID, 11, "https://www.instagram.com/reel/C1002/")

    # Clear in-memory queues
    for q in memory_queues.values():
        while not q.empty():
            q.get_nowait()

    # Reconcile queue from SQLite
    await reconcile_queue()

    # Verify jobs were restored to memory queue from SQLite
    queued_jobs = await get_all_queued_jobs()
    assert len(queued_jobs) == 2
    assert queued_jobs[0]["id"] == j1
    assert queued_jobs[1]["id"] == j2

@pytest.mark.asyncio
async def test_quota_record_delivery_atomic_idempotency():
    now = datetime.now(timezone.utc)
    j_id = await add_job(TEST_USER_ID, TEST_USER_ID, 1, "https://www.instagram.com/reel/C999/")

    # First delivery record succeeds and increments quota usage
    info1 = await record_successful_delivery(TEST_USER_ID, job_id=j_id, now=now)
    assert info1.success is True
    assert info1.usage == 1

    # Second delivery record with SAME job_id is idempotent (does NOT increment quota usage twice)
    info2 = await record_successful_delivery(TEST_USER_ID, job_id=j_id, now=now)
    assert info2.success is False
    assert info2.usage == 1

@pytest.mark.asyncio
async def test_subprocess_timeout_handling(monkeypatch):
    # Test that subprocess execution with timeout handles TimeoutError gracefully
    async def mock_communicate_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    # Test compress_video timeout
    mock_proc = MagicMock()
    mock_proc.communicate = mock_communicate_timeout
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

    res = await compress_video("/fake/input.mp4", "/fake/output.mp4")
    assert res is False
    mock_proc.kill.assert_called_once()

@pytest.mark.asyncio
async def test_sqlite_wal_mode_and_busy_timeout():
    import aiosqlite
    from src.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA journal_mode;") as cur:
            row = await cur.fetchone()
            assert row[0].lower() == "wal"

        async with db.execute("PRAGMA busy_timeout;") as cur:
            row = await cur.fetchone()
            assert row[0] == 5000
