import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.error import TelegramError

from src.config import config
from src.db import (
    init_db,
    add_job,
    get_job,
    get_admin_mode_user,
    upsert_admin_mode_user,
    update_admin_mode_status,
    update_admin_mode_current_mode,
    get_pending_submission_jobs,
    cancel_pending_submission_jobs,
    activate_pending_submission_jobs,
    cancel_user_jobs,
)
from src.services.admin_mode import (
    get_admin_mode_state,
    set_mode_to_normal,
    request_or_switch_to_admin_mode,
    approve_user_submission,
    disapprove_user_submission,
)
from src.handlers.admin_mode_handler import (
    mode_command,
    mode_toggle_callback,
    admin_submission_decision_callback,
)
from src.handlers.message_handler import handle_message
from src.utils.media_sender import send_downloaded_media

import pytest_asyncio
import os
import src.db as db_module

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path):
    db_file = str(tmp_path / "test_admin_mode.db")
    os.environ["DB_PATH"] = db_file
    db_module.DB_PATH = db_file
    await init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)

@pytest.mark.asyncio
async def test_db_admin_mode_crud():
    user_id = 991001

    # Initially none
    state = await get_admin_mode_user(user_id)
    assert state is None

    # Upsert PENDING
    await upsert_admin_mode_user(
        user_id=user_id,
        status="PENDING",
        current_mode="ADMIN",
        topic_id=456,
        user_name="John Doe"
    )
    row = await get_admin_mode_user(user_id)
    assert row["status"] == "PENDING"
    assert row["current_mode"] == "ADMIN"
    assert row["topic_id"] == 456
    assert row["user_name"] == "John Doe"

    # Update mode to NORMAL
    await update_admin_mode_current_mode(user_id, "NORMAL")
    row = await get_admin_mode_user(user_id)
    assert row["current_mode"] == "NORMAL"
    assert row["status"] == "PENDING"

    # Update status to APPROVED
    await update_admin_mode_status(user_id, status="APPROVED", current_mode="ADMIN")
    row = await get_admin_mode_user(user_id)
    assert row["status"] == "APPROVED"
    assert row["current_mode"] == "ADMIN"

@pytest.mark.asyncio
async def test_db_pending_submission_jobs():
    await init_db()
    user_id = 991002
    target_chat = -1004452680578
    topic_id = 789

    job_id = await add_job(
        user_id=user_id,
        chat_id=12345,
        message_id=10,
        url="https://instagram.com/reel/abc1234/",
        target_chat_id=target_chat,
        message_thread_id=topic_id,
        status="pending_approval"
    )

    pending = await get_pending_submission_jobs(user_id)
    assert len(pending) == 1
    assert pending[0]["id"] == job_id
    assert pending[0]["status"] == "pending_approval"

    # Activate
    activated = await activate_pending_submission_jobs(user_id, target_chat, topic_id)
    assert len(activated) == 1
    assert activated[0]["status"] == "queued"
    assert activated[0]["target_chat_id"] == target_chat
    assert activated[0]["message_thread_id"] == topic_id

    # Add another and cancel
    job_id2 = await add_job(
        user_id=user_id,
        chat_id=12345,
        message_id=11,
        url="https://instagram.com/reel/abc5678/",
        target_chat_id=target_chat,
        message_thread_id=topic_id,
        status="pending_approval"
    )
    cancelled_count = await cancel_pending_submission_jobs(user_id)
    assert cancelled_count == 1
    job2 = await get_job(job_id2)
    assert job2["status"] == "cancelled"

@pytest.mark.asyncio
async def test_request_admin_mode_and_topic_creation():
    await init_db()
    user_id = 991003
    bot = MagicMock()
    mock_topic = MagicMock()
    mock_topic.message_thread_id = 333
    bot.create_forum_topic = AsyncMock(return_value=mock_topic)
    bot.send_message = AsyncMock()

    user_info = {
        "first_name": "Alice",
        "last_name": "Smith",
        "username": "alicesmith"
    }

    success, msg = await request_or_switch_to_admin_mode(bot, user_id, user_info)
    assert success is True
    assert "Please wait for confirmation" in msg

    bot.create_forum_topic.assert_called_once()
    assert bot.create_forum_topic.call_args[1]["chat_id"] == config.admin_forum_group_id
    assert "Alice Smith | 991003" in bot.create_forum_topic.call_args[1]["name"]

    bot.send_message.assert_called_once()
    assert bot.send_message.call_args[1]["message_thread_id"] == 333

    state = await get_admin_mode_state(user_id)
    assert state["status"] == "PENDING"
    assert state["current_mode"] == "ADMIN"
    assert state["topic_id"] == 333

@pytest.mark.asyncio
async def test_approve_user_submission_workflow():
    await init_db()
    user_id = 991004
    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Setup user as PENDING with topic_id 444
    await upsert_admin_mode_user(
        user_id=user_id,
        status="PENDING",
        current_mode="ADMIN",
        topic_id=444,
        user_name="Bob"
    )
    # Add a pending job
    job_id = await add_job(
        user_id=user_id,
        chat_id=user_id,
        message_id=1,
        url="https://instagram.com/reel/bob1/",
        target_chat_id=config.admin_forum_group_id,
        message_thread_id=444,
        status="pending_approval"
    )

    with patch("src.services.admin_mode.enqueue_job", AsyncMock()) as mock_enqueue:
        success, msg = await approve_user_submission(bot, user_id, admin_name="AdminMaster")
        assert success is True

        # User notified in DM
        bot.send_message.assert_called_once_with(
            chat_id=user_id,
            text="🎉 Your request to send reels to admin has been approved! Your queued and upcoming reels will be sent directly to admin."
        )

        # Pending job activated and enqueued
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args[0][0]["job_id"] == job_id
        assert mock_enqueue.call_args[0][0]["target_chat_id"] == config.admin_forum_group_id
        assert mock_enqueue.call_args[0][0]["message_thread_id"] == 444

    state = await get_admin_mode_state(user_id)
    assert state["status"] == "APPROVED"
    assert state["current_mode"] == "ADMIN"

@pytest.mark.asyncio
async def test_disapprove_user_submission_workflow():
    await init_db()
    user_id = 991005
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.delete_forum_topic = AsyncMock()

    # Setup user as PENDING with topic_id 555
    await upsert_admin_mode_user(
        user_id=user_id,
        status="PENDING",
        current_mode="ADMIN",
        topic_id=555,
        user_name="Charlie"
    )
    # Add a pending job
    job_id = await add_job(
        user_id=user_id,
        chat_id=user_id,
        message_id=2,
        url="https://instagram.com/reel/charlie1/",
        target_chat_id=config.admin_forum_group_id,
        message_thread_id=555,
        status="pending_approval"
    )

    success, msg = await disapprove_user_submission(bot, user_id, admin_name="AdminMaster")
    assert success is True

    # User received exact decline message
    bot.send_message.assert_called_once_with(
        chat_id=user_id,
        text="Your submission request was declined. Switching back to normal download mode."
    )

    # Forum topic deleted
    bot.delete_forum_topic.assert_called_once_with(
        chat_id=config.admin_forum_group_id,
        message_thread_id=555
    )

    # Job was cancelled
    job = await get_job(job_id)
    assert job["status"] == "cancelled"

    state = await get_admin_mode_state(user_id)
    assert state["status"] == "DISAPPROVED"
    assert state["current_mode"] == "NORMAL"
    assert state["topic_id"] is None

@pytest.mark.asyncio
async def test_approved_user_mode_switching_retains_approval():
    await init_db()
    user_id = 991006
    bot = MagicMock()

    # Pre-approve user
    await upsert_admin_mode_user(
        user_id=user_id,
        status="APPROVED",
        current_mode="ADMIN",
        topic_id=666,
        user_name="Dave"
    )

    # Switch to NORMAL
    success, msg = await set_mode_to_normal(user_id)
    assert success is True
    state = await get_admin_mode_state(user_id)
    assert state["current_mode"] == "NORMAL"
    assert state["status"] == "APPROVED"
    assert state["topic_id"] == 666

    # Switch back to ADMIN without re-approval or creating new topic
    success, msg = await request_or_switch_to_admin_mode(bot, user_id, {"first_name": "Dave"})
    assert success is True
    assert "Switched to Send to Admin Mode" in msg
    bot.create_forum_topic.assert_not_called()

    state = await get_admin_mode_state(user_id)
    assert state["current_mode"] == "ADMIN"
    assert state["status"] == "APPROVED"

@pytest.mark.asyncio
async def test_disapproved_user_reapply_creates_new_topic():
    await init_db()
    user_id = 991007
    bot = MagicMock()
    mock_topic = MagicMock()
    mock_topic.message_thread_id = 777
    bot.create_forum_topic = AsyncMock(return_value=mock_topic)
    bot.send_message = AsyncMock()

    # User was disapproved
    await upsert_admin_mode_user(
        user_id=user_id,
        status="DISAPPROVED",
        current_mode="NORMAL",
        topic_id=None,
        user_name="Eve"
    )

    # Re-apply
    success, msg = await request_or_switch_to_admin_mode(bot, user_id, {"first_name": "Eve"})
    assert success is True
    assert "Please wait for confirmation" in msg
    bot.create_forum_topic.assert_called_once()
    assert bot.create_forum_topic.call_args[1]["name"] == "Eve | 991007"

    state = await get_admin_mode_state(user_id)
    assert state["status"] == "PENDING"
    assert state["current_mode"] == "ADMIN"
    assert state["topic_id"] == 777

@pytest.mark.asyncio
async def test_media_sender_strips_urls_for_topic():
    context = MagicMock()
    context.bot.send_video = AsyncMock()
    
    files = [{
        'path': '/tmp/test_video.mp4',
        'is_video': True,
        'caption': 'Check out this viral reel: https://www.instagram.com/reel/xyz123/ and subscribe! https://youtube.com/test'
    }]

    with patch("builtins.open", MagicMock()):
        with patch("src.utils.media_sender.get_cached_media", AsyncMock(return_value=None)):
            with patch("src.utils.media_sender.cache_media", AsyncMock()):
                await send_downloaded_media(
                    context,
                    chat_id=-1004452680578,
                    url="https://instagram.com/reel/xyz123/",
                    files=files,
                    message_thread_id=888
                )

    context.bot.send_video.assert_called_once()
    call_kwargs = context.bot.send_video.call_args[1]
    assert call_kwargs["chat_id"] == -1004452680578
    assert call_kwargs["message_thread_id"] == 888
    # Assert URLs were stripped from caption
    assert "https://" not in call_kwargs["caption"]
    assert "Check out this viral reel:  and subscribe!" in call_kwargs["caption"]

@pytest.mark.asyncio
async def test_message_handler_admin_mode_pending_intercepts_url():
    await init_db()
    user_id = 991008
    chat_id = 991008

    # Put user in PENDING mode
    await upsert_admin_mode_user(
        user_id=user_id,
        status="PENDING",
        current_mode="ADMIN",
        topic_id=999,
        user_name="Frank"
    )

    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "https://www.instagram.com/reel/pending_reel_123/"
    update.message.message_id = 101
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        with patch("src.handlers.message_handler.enqueue_job", AsyncMock()) as mock_enqueue:
            await handle_message(update, context)

            # Enqueue should NOT be called while pending
            mock_enqueue.assert_not_called()

            # Reply acknowledging pending status
            update.message.reply_text.assert_called_once_with(
                "⏳ Your submission has been queued and is waiting for admin approval."
            )

    pending_jobs = await get_pending_submission_jobs(user_id)
    assert len(pending_jobs) == 1
    assert pending_jobs[0]["status"] == "pending_approval"
    assert pending_jobs[0]["target_chat_id"] == config.admin_forum_group_id
    assert pending_jobs[0]["message_thread_id"] == 999

@pytest.mark.asyncio
async def test_message_handler_admin_mode_approved_bypasses_quota():
    await init_db()
    user_id = 991009
    chat_id = 991009

    # Put user in APPROVED mode
    await upsert_admin_mode_user(
        user_id=user_id,
        status="APPROVED",
        current_mode="ADMIN",
        topic_id=1111,
        user_name="Grace"
    )

    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = "https://www.instagram.com/reel/approved_reel_456/"
    update.message.message_id = 102
    proc_msg = MagicMock()
    proc_msg.message_id = 202
    update.message.reply_text = AsyncMock(return_value=proc_msg)

    context = MagicMock()

    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        with patch("src.handlers.message_handler.enqueue_job", AsyncMock()) as mock_enqueue:
            # Even if user would have failed normal quota, in approved admin mode it must pass!
            with patch("src.services.membership.can_user_download", AsyncMock(return_value=(False, "Quota limit reached"))):
                await handle_message(update, context)

                mock_enqueue.assert_called_once()
                job_arg = mock_enqueue.call_args[0][0]
                assert job_arg["user_id"] == user_id
                assert job_arg["target_chat_id"] == config.admin_forum_group_id
                assert job_arg["message_thread_id"] == 1111

    # Verify job status in DB
    job = await get_job(job_arg["job_id"])
    assert job["status"] == "queued"
    assert job["target_chat_id"] == config.admin_forum_group_id
    assert job["message_thread_id"] == 1111

@pytest.mark.asyncio
async def test_mode_command_and_toggles():
    await init_db()
    user_id = 991010
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("src.utils.access_control.is_user_allowed", AsyncMock(return_value=True)):
        await mode_command(update, context)
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Download Mode Selection" in text
        assert "Normal Download Mode" in text

    # Test mode_toggle_callback to admin
    cb_update = MagicMock()
    cb_update.callback_query.data = "set_mode:admin"
    cb_update.effective_user.id = user_id
    cb_update.effective_user.first_name = "Hannah"
    cb_update.effective_user.last_name = ""
    cb_update.effective_user.username = "hannah"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    mock_topic = MagicMock()
    mock_topic.message_thread_id = 1212
    context.bot.create_forum_topic = AsyncMock(return_value=mock_topic)
    context.bot.send_message = AsyncMock()

    await mode_toggle_callback(cb_update, context)
    cb_update.callback_query.answer.assert_called_once()
    cb_update.callback_query.edit_message_text.assert_called_once()

    state = await get_admin_mode_state(user_id)
    assert state["status"] == "PENDING"
    assert state["current_mode"] == "ADMIN"
    assert state["topic_id"] == 1212

@pytest.mark.asyncio
async def test_admin_decision_callback_unauthorized():
    cb_update = MagicMock()
    cb_update.callback_query.data = "approve_sub:991011"
    cb_update.callback_query.from_user.id = 999999999  # not an admin
    cb_update.callback_query.message.chat_id = 12345678  # not the forum supergroup
    cb_update.callback_query.answer = AsyncMock()
    context = MagicMock()

    with patch("src.handlers.admin_mode_handler.is_admin_db", AsyncMock(return_value=False)):
        await admin_submission_decision_callback(cb_update, context)
        cb_update.callback_query.answer.assert_called_once_with(
            "You are not authorized to make this decision.",
            show_alert=True
        )
