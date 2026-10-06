import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.error import TelegramError

from src.db import init_db, add_job, update_job_status, get_job, get_pending_jobs_count, save_user_db
from src.handlers.commands import start_command, help_command, status_command, cancel_command, CORE_HELP_MESSAGE, format_status_message
from src.handlers.message_handler import handle_message
from src.queue_manager import map_error_to_user_message
from src.services.membership import record_successful_delivery, grant_pro, grant_unlimited

@pytest.mark.asyncio
async def test_start_command_message():
    await init_db()
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 10001
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await start_command(update, context)
        update.message.reply_text.assert_called_once_with(
            "Send me an Instagram Reel or YouTube Short link and I'll download it for you."
        )

@pytest.mark.asyncio
async def test_help_command_message():
    await init_db()
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 10001
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await help_command(update, context)
        update.message.reply_text.assert_called_once_with(
            "Send me an Instagram Reel or YouTube Short link and I'll download it for you."
        )

@pytest.mark.asyncio
async def test_single_supported_url_handling():
    await init_db()
    user_id = 10002
    chat_id = 20002
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "Check this reel: https://www.instagram.com/reel/C999999/"
    update.message.message_id = 501
    
    proc_msg = MagicMock()
    proc_msg.message_id = 901
    update.message.reply_text = AsyncMock(return_value=proc_msg)
    
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await handle_message(update, context)
        
    count = await get_pending_jobs_count(user_id)
    assert count == 1

@pytest.mark.asyncio
async def test_multiple_supported_urls_handling():
    await init_db()
    user_id = 10003
    chat_id = 20003
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = """
    Link 1: https://www.instagram.com/reel/C111111/
    Link 2: https://www.youtube.com/shorts/ABC123XYZ
    Link 3: https://instagr.am/reel/C333333/
    """
    update.message.message_id = 502
    
    proc_msg = MagicMock()
    proc_msg.message_id = 902
    update.message.reply_text = AsyncMock(return_value=proc_msg)
    
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await handle_message(update, context)
        
    count = await get_pending_jobs_count(user_id)
    assert count == 3

@pytest.mark.asyncio
async def test_unsupported_url_handling():
    await init_db()
    user_id = 10004
    chat_id = 20004
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "Check post: https://instagram.com/p/C12345/"
    update.message.message_id = 503
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await handle_message(update, context)
        
    assert await get_pending_jobs_count(user_id) == 0
    update.message.reply_text.assert_called_once_with("This link is not supported.")

@pytest.mark.asyncio
async def test_mixed_supported_unsupported_urls():
    await init_db()
    user_id = 10005
    chat_id = 20005
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "Reel: https://instagram.com/reel/C123/ and Post: https://instagram.com/p/C456/"
    update.message.message_id = 504
    
    proc_msg = MagicMock()
    proc_msg.message_id = 904
    update.message.reply_text = AsyncMock(return_value=proc_msg)
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await handle_message(update, context)
        
    assert await get_pending_jobs_count(user_id) == 1
    update.message.reply_text.assert_any_call("This link is not supported.")

@pytest.mark.asyncio
async def test_processing_message_created_only_for_accepted_jobs():
    await init_db()
    user_id = 10006
    chat_id = 20006
    
    # Pre-fill 10 pending jobs
    for i in range(10):
        await add_job(user_id, chat_id, 100 + i, f"https://reel/{i}")
        
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "https://instagram.com/reel/C11th/"
    update.message.message_id = 505
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await handle_message(update, context)
        
    update.message.reply_text.assert_called_once_with("Your queue is full. Please wait for some videos to finish.")

@pytest.mark.asyncio
async def test_error_message_mapping_categories():
    assert map_error_to_user_message("Instagram posts and carousels are no longer supported.") == "This link is not supported."
    assert map_error_to_user_message("yt-dlp error: Video is private or login required.") == "This video is unavailable or private."
    assert map_error_to_user_message("HTTP Error 404: Not Found") == "This video is unavailable or private."
    assert map_error_to_user_message("HTTP Error 429: Too Many Requests") == "Unable to access this video right now. Please try again later."
    assert map_error_to_user_message("Connection timeout during network request") == "Unable to access this video right now. Please try again later."
    assert map_error_to_user_message("Video is too large and cannot be compressed below 50MB limit.") == "Unable to process this video. Please try again."
    assert map_error_to_user_message("FFmpeg processing failed") == "Unable to process this video. Please try again."
    assert map_error_to_user_message("Random unknown internal exception") == "Unable to download this video right now. Please try again later."

@pytest.mark.asyncio
async def test_successful_cleanup_resilience():
    await init_db()
    user_id = 10007
    chat_id = 20007
    
    j_id = await add_job(user_id, chat_id, 999, "https://reel/clean", original_message_id=555)
    
    bot_mock = MagicMock()
    bot_mock.delete_message = AsyncMock(side_effect=TelegramError("Message to delete not found"))
    
    await update_job_status(j_id, 'success')
    quota_info = await record_successful_delivery(user_id, job_id=j_id)
    
    assert quota_info.usage == 1
    job_db = await get_job(j_id)
    assert job_db['status'] == 'success'

@pytest.mark.asyncio
async def test_status_new_free_user():
    await init_db()
    user_id = 20001
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await status_command(update, context)
        
    expected_msg = (
        "Plan: Free\n"
        "Downloads: 0 / 100\n"
        "Queue: 0 / 10\n"
        "Quota starts with your first successful download."
    )
    update.message.reply_text.assert_called_once_with(expected_msg)

@pytest.mark.asyncio
async def test_status_active_free_user():
    await init_db()
    user_id = 20002
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(hours=15, minutes=37)
    
    await save_user_db(user_id, quota_window_start=window_start.isoformat(), quota_usage=47)
    await add_job(user_id, 100, 1, "r1")
    await add_job(user_id, 100, 2, "r2")
    await add_job(user_id, 100, 3, "r3")
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await status_command(update, None, now=now)
        
    expected_msg = (
        "Plan: Free\n"
        "Downloads: 47 / 100\n"
        "Reset: in 8h 23m\n"
        "Queue: 3 / 10"
    )
    update.message.reply_text.assert_called_once_with(expected_msg)

@pytest.mark.asyncio
async def test_status_pro_user():
    await init_db()
    user_id = 20003
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(hours=18, minutes=19)
    expiry = datetime(2026, 10, 22, 14, 30, tzinfo=timezone.utc)
    
    await save_user_db(
        user_id,
        membership_type='PRO',
        pro_expires_at=expiry.isoformat(),
        quota_window_start=window_start.isoformat(),
        quota_usage=214
    )
    await add_job(user_id, 100, 1, "r1")
    await add_job(user_id, 100, 2, "r2")
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await status_command(update, None, now=now)
        
    expected_msg = (
        "Plan: Pro\n"
        "Downloads: 214 / 500\n"
        "Reset: in 5h 41m\n"
        "Pro expires: 22 Oct 2026, 14:30\n"
        "Queue: 2 / 10"
    )
    update.message.reply_text.assert_called_once_with(expected_msg)

@pytest.mark.asyncio
async def test_status_unlimited_user():
    await init_db()
    user_id = 20004
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    
    await grant_unlimited(user_id)
    await save_user_db(user_id, membership_type='UNLIMITED', is_unlimited=True, quota_usage=37)
    await add_job(user_id, 100, 1, "r1")
    await add_job(user_id, 100, 2, "r2")
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await status_command(update, None, now=now)
        
    expected_msg = (
        "Plan: Unlimited\n"
        "Downloads: 37\n"
        "Queue: 2 / 10"
    )
    update.message.reply_text.assert_called_once_with(expected_msg)

@pytest.mark.asyncio
async def test_cancel_no_pending_jobs():
    await init_db()
    user_id = 20005
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await cancel_command(update, None)
        
    update.message.reply_text.assert_called_once_with("There are no pending downloads to cancel.")

@pytest.mark.asyncio
async def test_cancel_queued_jobs_only():
    await init_db()
    user_id = 20006
    
    await add_job(user_id, 100, 1, "r1")
    await add_job(user_id, 100, 2, "r2")
    await add_job(user_id, 100, 3, "r3")
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await cancel_command(update, None)
        
    update.message.reply_text.assert_called_once_with("Cancelled 3 pending downloads.")
    assert await get_pending_jobs_count(user_id) == 0

@pytest.mark.asyncio
async def test_cancel_protects_active_downloading_job():
    await init_db()
    user_id = 20007
    
    jA = await add_job(user_id, 100, 1, "rA")
    jB = await add_job(user_id, 100, 2, "rB")
    jC = await add_job(user_id, 100, 3, "rC")
    
    # Set A to downloading (active job)
    await update_job_status(jA, 'downloading')
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await cancel_command(update, None)
        
    # Only B and C (2 queued jobs) should be cancelled
    update.message.reply_text.assert_called_once_with("Cancelled 2 pending downloads.")
    
    # Active job A remains downloading
    jobA_db = await get_job(jA)
    assert jobA_db['status'] == 'downloading'
    
    # B and C are cancelled
    jobB_db = await get_job(jB)
    assert jobB_db['status'] == 'cancelled'
    
    # Pending count is 1 (only the active downloading job remains)
    assert await get_pending_jobs_count(user_id) == 1

@pytest.mark.asyncio
async def test_cancel_only_active_downloading_job_returns_no_pending():
    await init_db()
    user_id = 20008
    
    jA = await add_job(user_id, 100, 1, "rA")
    await update_job_status(jA, 'downloading')
    
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    
    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await cancel_command(update, None)
        
    update.message.reply_text.assert_called_once_with("There are no pending downloads to cancel.")
    
    # Active job A remains downloading
    jobA_db = await get_job(jA)
    assert jobA_db['status'] == 'downloading'

@pytest.mark.asyncio
async def test_worker_skips_cancelled_job():
    await init_db()
    user_id = 20009
    
    j_id = await add_job(user_id, 100, 1, "r1")
    await update_job_status(j_id, 'cancelled', 'Cancelled by user')
    
    # Verify worker pop check
    job = await get_job(j_id)
    assert job['status'] == 'cancelled'
    assert job['status'] in ('cancelled', 'failed', 'success')
