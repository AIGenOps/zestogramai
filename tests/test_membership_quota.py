import pytest
import pytest_asyncio
import os
import asyncio
from datetime import datetime, timedelta, timezone

import src.db as db_module
from src.db import (
    init_db,
    get_user_db,
    save_user_db,
    add_job,
    update_job_status,
    get_pending_jobs_count,
    mark_job_quota_recorded
)
from src.services.membership import (
    Plan,
    get_user_membership,
    get_effective_plan,
    get_user_quota_info,
    can_user_download,
    check_user_can_submit,
    record_successful_delivery,
    grant_pro,
    grant_unlimited,
    revoke_membership,
)
from src.config import FREE_LIMIT, PRO_LIMIT

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path):
    db_file = str(tmp_path / "test_zestogram.db")
    os.environ["DB_PATH"] = db_file
    db_module.DB_PATH = db_file
    await init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)

@pytest.mark.asyncio
async def test_1_new_user_is_free():
    user_id = 1001
    membership = await get_user_membership(user_id)
    effective_plan = await get_effective_plan(user_id)
    assert membership.membership_type == Plan.FREE
    assert effective_plan == Plan.FREE

@pytest.mark.asyncio
async def test_2_new_user_has_0_usage():
    user_id = 1002
    info = await get_user_quota_info(user_id)
    assert info.usage == 0
    assert info.window_start is None
    assert info.limit == FREE_LIMIT
    assert info.remaining == FREE_LIMIT

@pytest.mark.asyncio
async def test_3_first_successful_delivery_starts_quota_window():
    user_id = 1003
    quota_res = await record_successful_delivery(user_id)
    assert quota_res.success is True
    info = await get_user_quota_info(user_id)
    assert info.usage == 1
    assert info.window_start is not None

@pytest.mark.asyncio
async def test_4_successful_delivery_increments_usage():
    user_id = 1004
    await record_successful_delivery(user_id)
    await record_successful_delivery(user_id)
    info = await get_user_quota_info(user_id)
    assert info.usage == 2

@pytest.mark.asyncio
async def test_5_failed_operation_does_not_increment_usage():
    user_id = 1005
    # No record_successful_delivery called (simulating download or queue failure)
    info = await get_user_quota_info(user_id)
    assert info.usage == 0

@pytest.mark.asyncio
async def test_6_usage_reaches_100_for_free():
    user_id = 1006
    for _ in range(100):
        q = await record_successful_delivery(user_id)
        assert q.success is True
    info = await get_user_quota_info(user_id)
    assert info.usage == 100
    assert info.remaining == 0

@pytest.mark.asyncio
async def test_7_free_user_cannot_exceed_100():
    user_id = 1007
    for _ in range(100):
        await record_successful_delivery(user_id)
    
    can_dl, reason = await can_user_download(user_id)
    assert can_dl is False
    assert "Quota limit" in reason

    # Attempting 101st delivery returns success=False and does not increment usage beyond limit
    q101 = await record_successful_delivery(user_id)
    assert q101.success is False
    info = await get_user_quota_info(user_id)
    assert info.usage == 100

@pytest.mark.asyncio
async def test_8_pro_user_has_limit_500():
    user_id = 1008
    await grant_pro(user_id)
    info = await get_user_quota_info(user_id)
    assert info.effective_plan == Plan.PRO
    assert info.limit == PRO_LIMIT
    assert info.remaining == PRO_LIMIT

@pytest.mark.asyncio
async def test_9_pro_grant_gives_30_days():
    user_id = 1009
    now = datetime.now(timezone.utc)
    await grant_pro(user_id)
    user = await get_user_db(user_id)
    pro_exp = datetime.fromisoformat(user["pro_expires_at"])
    expected_exp = now + timedelta(days=30)
    diff = abs((pro_exp - expected_exp).total_seconds())
    assert diff < 10

@pytest.mark.asyncio
async def test_10_active_pro_renewal_adds_30_days():
    user_id = 1010
    now = datetime.now(timezone.utc)
    await grant_pro(user_id)
    user_first = await get_user_db(user_id)
    exp1 = datetime.fromisoformat(user_first["pro_expires_at"])
    
    await grant_pro(user_id)
    user_second = await get_user_db(user_id)
    exp2 = datetime.fromisoformat(user_second["pro_expires_at"])
    
    expected_exp2 = exp1 + timedelta(days=30)
    diff = abs((exp2 - expected_exp2).total_seconds())
    assert diff < 5

@pytest.mark.asyncio
async def test_11_expired_pro_renewal_starts_from_current_time():
    user_id = 1011
    # Set an expired pro_expires_at (10 days ago)
    past_exp = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    await save_user_db(user_id, membership_type="pro", pro_expires_at=past_exp)
    
    now = datetime.now(timezone.utc)
    await grant_pro(user_id)
    user = await get_user_db(user_id)
    exp = datetime.fromisoformat(user["pro_expires_at"])
    
    expected_exp = now + timedelta(days=30)
    diff = abs((exp - expected_exp).total_seconds())
    assert diff < 10

@pytest.mark.asyncio
async def test_12_expired_pro_behaves_as_free():
    user_id = 1012
    past_exp = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    await save_user_db(user_id, membership_type="pro", pro_expires_at=past_exp)
    
    effective_plan = await get_effective_plan(user_id)
    assert effective_plan == Plan.FREE
    
    info = await get_user_quota_info(user_id)
    assert info.limit == FREE_LIMIT

@pytest.mark.asyncio
async def test_13_unlimited_has_no_quota_restriction():
    user_id = 1013
    await grant_unlimited(user_id)
    
    # Record more than PRO limit
    for _ in range(505):
        q = await record_successful_delivery(user_id)
        assert q.success is True
        
    can_dl, _ = await can_user_download(user_id)
    assert can_dl is True
    info = await get_user_quota_info(user_id)
    assert info.remaining == float("inf")

@pytest.mark.asyncio
async def test_14_unlimited_overrides_pro():
    user_id = 1014
    await grant_unlimited(user_id)
    # Set pro_expires_at or membership_type
    await save_user_db(user_id, is_unlimited=1, membership_type="pro")
    
    effective_plan = await get_effective_plan(user_id)
    assert effective_plan == Plan.UNLIMITED

@pytest.mark.asyncio
async def test_15_pro_to_unlimited_immediately_replaces_pro():
    user_id = 1015
    await grant_pro(user_id)
    await grant_unlimited(user_id)
    
    user = await get_user_db(user_id)
    assert user["is_unlimited"] == 1
    assert user["pro_expires_at"] is None
    assert user["membership_type"] == Plan.UNLIMITED
    assert await get_effective_plan(user_id) == Plan.UNLIMITED

@pytest.mark.asyncio
async def test_16_expired_quota_window_is_recognized_correctly():
    user_id = 1016
    old_start = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await save_user_db(user_id, quota_window_start=old_start, quota_usage=50)
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 0
    assert info.remaining == FREE_LIMIT

@pytest.mark.asyncio
async def test_17_next_successful_delivery_after_expiry_starts_new_window():
    user_id = 1017
    old_start = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await save_user_db(user_id, quota_window_start=old_start, quota_usage=50)
    
    now = datetime.now(timezone.utc)
    await record_successful_delivery(user_id)
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 1
    diff = abs((info.window_start - now).total_seconds())
    assert diff < 10

@pytest.mark.asyncio
async def test_18_multiple_successful_deliveries_do_not_move_original_window_start():
    user_id = 1018
    await record_successful_delivery(user_id)
    info1 = await get_user_quota_info(user_id)
    w_start1 = info1.window_start
    
    await record_successful_delivery(user_id)
    await record_successful_delivery(user_id)
    
    info2 = await get_user_quota_info(user_id)
    assert info2.window_start == w_start1
    assert info2.usage == 3

@pytest.mark.asyncio
async def test_19_concurrent_successful_deliveries():
    user_id = 1019
    # Simulate 110 concurrent delivery attempts for a Free user (limit 100)
    results = await asyncio.gather(*[record_successful_delivery(user_id) for _ in range(110)])
    
    success_count = sum(1 for q in results if q.success is True)
    assert success_count == 100
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 100

# Additional tests for Section 12 correctness & hardening audit:

@pytest.mark.asyncio
async def test_20_multiple_jobs_submitted_before_any_delivery_completes():
    user_id = 2001
    # Set usage to 95 for Free user (limit 100)
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=95)
    
    accepted = 0
    rejected = 0
    for i in range(10):
        can_dl, _ = await can_user_download(user_id)
        if can_dl:
            await add_job(user_id, 100, 100+i, f"https://reel/{i}")
            accepted += 1
        else:
            rejected += 1
            
    assert accepted == 5
    assert rejected == 5
    assert await get_pending_jobs_count(user_id) == 5

@pytest.mark.asyncio
async def test_21_user_at_99_submitting_multiple_jobs():
    user_id = 2002
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=99)
    
    can1, _ = await can_user_download(user_id)
    assert can1 is True
    await add_job(user_id, 100, 201, "https://reel/1")
    
    can2, _ = await can_user_download(user_id)
    assert can2 is False

@pytest.mark.asyncio
async def test_22_two_simultaneous_submissions_at_quota_boundary():
    user_id = 2003
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=99)
    
    async def try_submit(idx):
        can_dl, _ = await can_user_download(user_id)
        if can_dl:
            await add_job(user_id, 100, 300 + idx, f"https://reel/{idx}")
            return True
        return False

    results = await asyncio.gather(try_submit(1), try_submit(2))
    assert sum(1 for r in results if r) == 1
    assert await get_pending_jobs_count(user_id) == 1

@pytest.mark.asyncio
async def test_23_pending_quota_capacity():
    user_id = 2004
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=10)
    for i in range(5):
        await add_job(user_id, 100, 400 + i, f"https://reel/{i}")
        
    info = await get_user_quota_info(user_id)
    assert info.usage == 10
    assert info.pending_jobs == 5
    assert info.remaining == 85

@pytest.mark.asyncio
async def test_24_failed_download_releases_reservation():
    user_id = 2005
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=99)
    job_id = await add_job(user_id, 100, 501, "https://reel/fail")
    
    can_before, _ = await can_user_download(user_id)
    assert can_before is False
    
    # Download fails -> job status updated to 'failed'
    await update_job_status(job_id, 'failed', 'Network error')
    
    can_after, _ = await can_user_download(user_id)
    assert can_after is True

@pytest.mark.asyncio
async def test_25_failed_processing_releases_reservation():
    user_id = 2006
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=99)
    job_id = await add_job(user_id, 100, 601, "https://reel/proc_fail")
    
    await update_job_status(job_id, 'failed', 'FFmpeg compression failed')
    
    can_after, _ = await can_user_download(user_id)
    assert can_after is True

@pytest.mark.asyncio
async def test_26_failed_telegram_delivery_releases_reservation():
    user_id = 2007
    await save_user_db(user_id, quota_window_start=datetime.now(timezone.utc).isoformat(), quota_usage=99)
    job_id = await add_job(user_id, 100, 701, "https://reel/tg_fail")
    
    # Telegram delivery fails -> record_successful_delivery NOT called
    await update_job_status(job_id, 'failed', 'Telegram API 400 Bad Request')
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 99
    assert info.pending_jobs == 0
    can_after, _ = await can_user_download(user_id)
    assert can_after is True

@pytest.mark.asyncio
async def test_27_successful_delivery_permanently_consumes_quota():
    user_id = 2008
    job_id = await add_job(user_id, 100, 801, "https://reel/success")
    
    await record_successful_delivery(user_id, job_id=job_id)
    await update_job_status(job_id, 'success')
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 1
    assert info.pending_jobs == 0

@pytest.mark.asyncio
async def test_28_duplicate_successful_delivery_does_not_double_count():
    user_id = 2009
    job_id = await add_job(user_id, 100, 901, "https://reel/dup")
    
    q1 = await record_successful_delivery(user_id, job_id=job_id)
    assert q1.usage == 1
    
    # Duplicate call for same job_id
    q2 = await record_successful_delivery(user_id, job_id=job_id)
    assert q2.usage == 1
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 1

@pytest.mark.asyncio
async def test_29_process_safe_database_idempotency():
    job_id = await add_job(2009, 100, 999, "https://reel/test_idem")
    first = await mark_job_quota_recorded(job_id)
    second = await mark_job_quota_recorded(job_id)
    
    assert first is True
    assert second is False

@pytest.mark.asyncio
async def test_30_expired_quota_window_with_pending_jobs():
    user_id = 2010
    old_start = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await save_user_db(user_id, quota_window_start=old_start, quota_usage=50)
    await add_job(user_id, 100, 1001, "https://reel/pending")
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 0
    assert info.pending_jobs == 1
    assert info.remaining == 99

@pytest.mark.asyncio
async def test_31_free_to_pro_preserves_usage():
    user_id = 2011
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(user_id, quota_window_start=now_str, quota_usage=70)
    
    await grant_pro(user_id)
    info = await get_user_quota_info(user_id)
    assert info.effective_plan == Plan.PRO
    assert info.usage == 70
    assert info.limit == PRO_LIMIT
    assert info.remaining == 430

@pytest.mark.asyncio
async def test_32_pro_to_unlimited_preserves_usage():
    user_id = 2012
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(user_id, quota_window_start=now_str, quota_usage=200)
    
    await grant_unlimited(user_id)
    info = await get_user_quota_info(user_id)
    assert info.effective_plan == Plan.UNLIMITED
    assert info.usage == 200
    assert info.limit == float('inf')
    assert info.remaining == float('inf')

@pytest.mark.asyncio
async def test_33_pro_expiry_without_cron():
    user_id = 2013
    now = datetime.now(timezone.utc)
    expired_exp = (now - timedelta(hours=1)).isoformat()
    now_str = now.isoformat()
    await save_user_db(user_id, membership_type="pro", pro_expires_at=expired_exp, quota_window_start=now_str, quota_usage=70)
    
    info = await get_user_quota_info(user_id)
    assert info.effective_plan == Plan.FREE
    assert info.limit == FREE_LIMIT
    assert info.usage == 70
    assert info.remaining == 30

@pytest.mark.asyncio
async def test_34_stale_downloading_job_recovery():
    user_id = 2014
    job_id = await add_job(user_id, 100, 3401, "https://reel/stale")
    await update_job_status(job_id, 'downloading')
    
    # Verify pending count is 1 before recovery
    assert await get_pending_jobs_count(user_id) == 1
    
    # Run recovery (simulating system restart)
    from src.db import recover_stale_jobs, get_job
    recovered_count = await recover_stale_jobs()
    assert recovered_count >= 1
    
    # Pending count is freed and job marked failed
    assert await get_pending_jobs_count(user_id) == 0
    job = await get_job(job_id)
    assert job['status'] == 'failed'
    assert 'Interrupted by system restart' in job['error_reason']

@pytest.mark.asyncio
async def test_35_concurrent_deliveries_same_job_id():
    user_id = 2015
    job_id = await add_job(user_id, 100, 3501, "https://reel/concurrent_same_job")
    
    # Run 5 simultaneous delivery recordings for the exact same job_id
    results = await asyncio.gather(*[record_successful_delivery(user_id, job_id=job_id) for _ in range(5)])
    
    success_flags = [q.success for q in results]
    assert sum(1 for s in success_flags if s) == 1
    
    info = await get_user_quota_info(user_id)
    assert info.usage == 1

@pytest.mark.asyncio
async def test_36_free_to_unlimited_preserves_usage():
    user_id = 2016
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(user_id, quota_window_start=now_str, quota_usage=50)
    
    await grant_unlimited(user_id)
    info = await get_user_quota_info(user_id)
    assert info.effective_plan == Plan.UNLIMITED
    assert info.usage == 50
    assert info.limit == float('inf')
    assert info.remaining == float('inf')

@pytest.mark.asyncio
async def test_37_multiple_users_concurrent_quota_tracking():
    user_a = 2017
    user_b = 2018
    
    await record_successful_delivery(user_a)
    await record_successful_delivery(user_b)
    await record_successful_delivery(user_b)
    
    info_a = await get_user_quota_info(user_a)
    info_b = await get_user_quota_info(user_b)
    
    assert info_a.usage == 1
    assert info_b.usage == 2

@pytest.mark.asyncio
async def test_38_user_at_100_with_pending_jobs():
    user_id = 2019
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(user_id, quota_window_start=now_str, quota_usage=100)
    await add_job(user_id, 100, 3801, "https://reel/p1")
    await add_job(user_id, 100, 3802, "https://reel/p2")
    
    can_dl, reason = await can_user_download(user_id)
    assert can_dl is False
    assert "Quota limit (100) reached" in reason
    assert "100 delivered, 2 pending" in reason

@pytest.mark.asyncio
async def test_39_queue_limit_10_pending_jobs_accepted_11th_rejected():
    user_id = 3001
    from src.services.membership import can_user_queue_job
    
    # 0 to 10 jobs accepted
    for i in range(10):
        can_q, _ = await can_user_queue_job(user_id)
        assert can_q is True
        await add_job(user_id, 100, 3900 + i, f"https://reel/{i}")
        
    assert await get_pending_jobs_count(user_id) == 10
    
    # 11th job rejected
    can_q_11, reason = await can_user_queue_job(user_id)
    assert can_q_11 is False
    assert "Your queue is full. Please wait for some videos to finish." in reason

@pytest.mark.asyncio
async def test_40_queue_slot_released_on_job_terminal_states():
    user_id = 3002
    from src.services.membership import can_user_queue_job
    
    job_ids = []
    for i in range(10):
        jid = await add_job(user_id, 100, 4000 + i, f"https://reel/{i}")
        job_ids.append(jid)
        
    assert (await can_user_queue_job(user_id))[0] is False
    
    # Success releases slot
    await update_job_status(job_ids[0], 'success')
    assert (await can_user_queue_job(user_id))[0] is True
    j11 = await add_job(user_id, 100, 4011, "https://reel/11")
    assert (await can_user_queue_job(user_id))[0] is False
    
    # Failed releases slot
    await update_job_status(job_ids[1], 'failed', 'Error')
    assert (await can_user_queue_job(user_id))[0] is True
    j12 = await add_job(user_id, 100, 4012, "https://reel/12")
    assert (await can_user_queue_job(user_id))[0] is False
    
    # Cancelled releases slot
    await update_job_status(job_ids[2], 'cancelled', 'User cancelled')
    assert (await can_user_queue_job(user_id))[0] is True

@pytest.mark.asyncio
async def test_41_concurrent_submissions_cannot_exceed_10_queue_limit():
    user_id = 3003
    from src.services.membership import submit_job_if_allowed
    
    async def try_submit(idx):
        allowed, _, _ = await submit_job_if_allowed(user_id, 100, 4100 + idx, f"https://reel/{idx}")
        return allowed

    results = await asyncio.gather(*[try_submit(i) for i in range(15)])
    accepted = sum(1 for r in results if r)
    assert accepted == 10
    assert await get_pending_jobs_count(user_id) == 10

@pytest.mark.asyncio
async def test_42_membership_priority_queue_order():
    user_unlimited = 3004
    user_pro = 3005
    user_free = 3006
    
    await grant_unlimited(user_unlimited)
    await grant_pro(user_pro)
    
    from src.queue_manager import enqueue_job, pop_job
    import src.queue_manager as qm
    qm._pop_counter = 0
    
    j_unlim_id = await add_job(user_unlimited, 100, 4201, "https://reel/u1")
    j_pro_id = await add_job(user_pro, 100, 4202, "https://reel/p1")
    j_free_id = await add_job(user_free, 100, 4203, "https://reel/f1")
    
    job_unlimited = {'job_id': j_unlim_id, 'user_id': user_unlimited, 'chat_id': 100, 'url': 'u1', 'message_id': 1, 'original_message_id': 1}
    job_pro = {'job_id': j_pro_id, 'user_id': user_pro, 'chat_id': 100, 'url': 'p1', 'message_id': 2, 'original_message_id': 2}
    job_free = {'job_id': j_free_id, 'user_id': user_free, 'chat_id': 100, 'url': 'f1', 'message_id': 3, 'original_message_id': 3}
    
    await enqueue_job(job_free)
    await enqueue_job(job_pro)
    await enqueue_job(job_unlimited)
    
    # First pop in cycle 0 should pick Unlimited
    popped1 = await pop_job()
    assert popped1['user_id'] == user_unlimited
    
    # Second pop in cycle 1 should pick Pro
    popped2 = await pop_job()
    assert popped2['user_id'] == user_pro
    
    # Third pop should pick Free
    popped3 = await pop_job()
    assert popped3['user_id'] == user_free

@pytest.mark.asyncio
async def test_43_fair_priority_anti_starvation():
    user_unlimited = 3007
    user_free = 3008
    await grant_unlimited(user_unlimited)
    
    from src.queue_manager import enqueue_job, pop_job
    import src.queue_manager as qm
    qm._pop_counter = 0
    
    # Enqueue 10 Unlimited jobs and 2 Free jobs
    for i in range(10):
        jid = await add_job(user_unlimited, 100, 4300+i, "https://reel/u")
        await enqueue_job({'job_id': jid, 'user_id': user_unlimited, 'chat_id': 100, 'url': 'u', 'message_id': i, 'original_message_id': i})
    for i in range(2):
        jid = await add_job(user_free, 100, 4350+i, "https://reel/f")
        await enqueue_job({'job_id': jid, 'user_id': user_free, 'chat_id': 100, 'url': 'f', 'message_id': i, 'original_message_id': i})
        
    popped_tiers = []
    for _ in range(9):
        j = await pop_job()
        if j:
            popped_tiers.append(j['tier'])
            
    # In 9 cycles (5 unlimited, 3 pro fallback to unlimited, 1 free), free tier gets 1 pop!
    assert 'free' in popped_tiers

@pytest.mark.asyncio
async def test_44_same_user_fifo_execution():
    user_id = 3009
    j1 = await add_job(user_id, 100, 4401, "https://reel/1")
    j2 = await add_job(user_id, 100, 4402, "https://reel/2")
    
    await update_job_status(j1, 'downloading')
    
    # Check if j2 has earlier pending job (j1 is downloading)
    from src.db import has_earlier_pending_job
    assert await has_earlier_pending_job(user_id, j2) is True
    
    # Mark j1 as success
    await update_job_status(j1, 'success')
    
    # Now j2 can execute
    assert await has_earlier_pending_job(user_id, j2) is False

@pytest.mark.asyncio
async def test_45_combined_quota_and_queue_limits():
    user_id = 3010
    from src.services.membership import can_user_queue_job
    
    # Test 1: Quota limit reached (95 usage + 5 pending = 100), queue limit (5/10) not reached
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(user_id, quota_window_start=now_str, quota_usage=95)
    for i in range(5):
        await add_job(user_id, 100, 4500 + i, f"https://reel/{i}")
        
    assert (await can_user_queue_job(user_id))[0] is True
    assert (await can_user_download(user_id))[0] is False
    assert (await check_user_can_submit(user_id))[0] is False

    # Test 2: Quota limit (20 usage + 10 pending < 100) not reached, queue limit (10/10) reached
    user2 = 3011
    await save_user_db(user2, quota_window_start=now_str, quota_usage=20)
    for i in range(10):
        await add_job(user2, 100, 4550 + i, f"https://reel/{i}")
        
    assert (await can_user_queue_job(user2))[0] is False
    assert (await can_user_download(user2))[0] is True
    assert (await check_user_can_submit(user2))[0] is False

@pytest.mark.asyncio
async def test_46_multi_worker_same_user_serialized_execution():
    user_id = 3012
    from src.db import has_earlier_pending_job
    
    jA = await add_job(user_id, 100, 4601, "https://reel/A")
    jB = await add_job(user_id, 100, 4602, "https://reel/B")
    jC = await add_job(user_id, 100, 4603, "https://reel/C")
    
    # Initially A is queued. B and C observe earlier pending job.
    assert await has_earlier_pending_job(user_id, jA) is False
    assert await has_earlier_pending_job(user_id, jB) is True
    assert await has_earlier_pending_job(user_id, jC) is True
    
    # Worker 1 sets A to downloading
    await update_job_status(jA, 'downloading')
    assert await has_earlier_pending_job(user_id, jB) is True
    assert await has_earlier_pending_job(user_id, jC) is True
    
    # A completes
    await update_job_status(jA, 'success')
    assert await has_earlier_pending_job(user_id, jB) is False
    assert await has_earlier_pending_job(user_id, jC) is True
    
    # Worker 2 sets B to downloading
    await update_job_status(jB, 'downloading')
    assert await has_earlier_pending_job(user_id, jC) is True
    
    # B completes
    await update_job_status(jB, 'success')
    assert await has_earlier_pending_job(user_id, jC) is False

@pytest.mark.asyncio
async def test_47_fifo_releases_on_earlier_job_failure_or_cancellation():
    user_id = 3013
    from src.db import has_earlier_pending_job
    
    jA = await add_job(user_id, 100, 4701, "https://reel/A")
    jB = await add_job(user_id, 100, 4702, "https://reel/B")
    jC = await add_job(user_id, 100, 4703, "https://reel/C")
    
    # Job A fails
    await update_job_status(jA, 'failed', 'Network error')
    assert await has_earlier_pending_job(user_id, jB) is False
    assert await has_earlier_pending_job(user_id, jC) is True
    
    # Job B is cancelled by user
    await update_job_status(jB, 'cancelled', 'User cancelled')
    assert await has_earlier_pending_job(user_id, jC) is False

@pytest.mark.asyncio
async def test_48_repeated_deferral_queue_growth_prevention():
    user_id = 3014
    from src.queue_manager import enqueue_job, pop_job, get_queue_length
    import src.queue_manager as qm
    qm.use_memory_queue = True
    for q in qm.memory_queues.values():
        while not q.empty():
            q.get_nowait()
    
    jA = await add_job(user_id, 100, 4801, "https://reel/A")
    jB = await add_job(user_id, 100, 4802, "https://reel/B")
    
    await update_job_status(jA, 'downloading')
    
    job_data_B = {'job_id': jB, 'user_id': user_id, 'chat_id': 100, 'url': 'B', 'message_id': 2, 'original_message_id': 2}
    await enqueue_job(job_data_B)
    
    initial_len = await get_queue_length()
    
    # Repeatedly pop and defer B 5 times
    for _ in range(5):
        popped = await pop_job()
        assert popped['job_id'] == jB
        from src.db import has_earlier_pending_job
        assert await has_earlier_pending_job(user_id, jB) is True
        await enqueue_job(popped)
        
    final_len = await get_queue_length()
    assert final_len == initial_len == 1

@pytest.mark.asyncio
async def test_49_memory_queue_multi_tier_behavior():
    import src.queue_manager as qm
    qm.use_memory_queue = True
    qm._pop_counter = 0
    for q in qm.memory_queues.values():
        while not q.empty():
            q.get_nowait()
    
    user_unlimited = 3015
    user_pro = 3016
    user_free = 3017
    await grant_unlimited(user_unlimited)
    await grant_pro(user_pro)
    
    j1 = await add_job(user_unlimited, 100, 4901, "u")
    j2 = await add_job(user_pro, 100, 4902, "p")
    j3 = await add_job(user_free, 100, 4903, "f")
    
    await qm.enqueue_job({'job_id': j1, 'user_id': user_unlimited, 'chat_id': 100, 'url': 'u', 'message_id': 1, 'original_message_id': 1})
    await qm.enqueue_job({'job_id': j2, 'user_id': user_pro, 'chat_id': 100, 'url': 'p', 'message_id': 2, 'original_message_id': 2})
    await qm.enqueue_job({'job_id': j3, 'user_id': user_free, 'chat_id': 100, 'url': 'f', 'message_id': 3, 'original_message_id': 3})
    
    p1 = await qm.pop_job()
    assert p1['tier'] == 'unlimited'
    p2 = await qm.pop_job()
    assert p2['tier'] == 'pro'
    p3 = await qm.pop_job()
    assert p3['tier'] == 'free'

@pytest.mark.asyncio
async def test_50_concurrent_submissions_boundary_9_and_10():
    user_id = 3018
    from src.services.membership import submit_job_if_allowed
    
    # Pre-fill 9 jobs
    for i in range(9):
        await add_job(user_id, 100, 5000 + i, f"https://reel/{i}")
        
    assert await get_pending_jobs_count(user_id) == 9
    
    # 10 concurrent submissions when at 9 -> exactly 1 accepted
    results = await asyncio.gather(*[
        submit_job_if_allowed(user_id, 100, 5020 + i, f"https://reel/new_{i}")
        for i in range(10)
    ])
    accepted = sum(1 for res in results if res[0])
    assert accepted == 1
    assert await get_pending_jobs_count(user_id) == 10
    
    # 10 concurrent submissions when at 10 -> exactly 0 accepted
    results2 = await asyncio.gather(*[
        submit_job_if_allowed(user_id, 100, 5040 + i, f"https://reel/over_{i}")
        for i in range(10)
    ])
    accepted2 = sum(1 for res in results2 if res[0])
    assert accepted2 == 0
    assert await get_pending_jobs_count(user_id) == 10

@pytest.mark.asyncio
async def test_51_empty_tier_fallback():
    import src.queue_manager as qm
    qm.use_memory_queue = True
    qm._pop_counter = 0
    for q in qm.memory_queues.values():
        while not q.empty():
            q.get_nowait()
    
    user_free = 3019
    j1 = await add_job(user_free, 100, 5101, "f1")
    await qm.enqueue_job({'job_id': j1, 'user_id': user_free, 'chat_id': 100, 'url': 'f1', 'message_id': 1, 'original_message_id': 1})
    
    # Unlimited and Pro queues are empty. Pop should fallback to Free immediately on cycle 0
    popped = await qm.pop_job()
    assert popped is not None
    assert popped['tier'] == 'free'

@pytest.mark.asyncio
async def test_52_membership_change_while_jobs_queued():
    user_id = 3020
    from src.db import has_earlier_pending_job
    
    # User submits A and B as Free
    jA = await add_job(user_id, 100, 5201, "https://reel/A")
    jB = await add_job(user_id, 100, 5202, "https://reel/B")
    
    # Upgrade user to Unlimited
    await grant_unlimited(user_id)
    
    # FIFO check still requires jA to complete before jB
    await update_job_status(jA, 'downloading')
    assert await has_earlier_pending_job(user_id, jB) is True
    
    await update_job_status(jA, 'success')
    assert await has_earlier_pending_job(user_id, jB) is False

@pytest.mark.asyncio
async def test_53_process_restart_and_queue_reconciliation():
    user_id = 3021
    from src.db import get_job
    from src.queue_manager import reconcile_queue, get_queue_length
    import src.queue_manager as qm
    qm.use_memory_queue = True
    
    # Clear memory queues
    for q in qm.memory_queues.values():
        while not q.empty():
            q.get_nowait()
            
    j1 = await add_job(user_id, 100, 5301, "downloading_job")
    await update_job_status(j1, 'downloading')
    
    j2 = await add_job(user_id, 100, 5302, "queued_job_1")
    j3 = await add_job(user_id, 100, 5303, "queued_job_2")
    
    await reconcile_queue()
    
    # j1 should be marked failed due to restart
    job1_db = await get_job(j1)
    assert job1_db['status'] == 'failed'
    assert 'Interrupted by system restart' in job1_db['error_reason']
    
    # j2 and j3 should be re-enqueued
    assert await get_queue_length() == 2




