import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import src.db as db_module
from src.config import config
from src.db import init_db, save_user_db, get_job, get_pending_jobs_count
from src.services.membership import (
    record_successful_delivery,
    get_user_membership,
    grant_pro,
    grant_unlimited,
    Plan
)
from src.services.payment import format_payment_instructions, send_upgrade_prompt
from src.handlers.message_handler import handle_message

USER_ID_FREE = 888111
USER_ID_PRO = 888222
USER_ID_UNLIMITED = 888333

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_payment.db")
    monkeypatch.setenv("DB_PATH", test_db)
    monkeypatch.setattr(config, "allowed_user_ids", "")
    monkeypatch.setattr(config, "allowed_chat_ids", "")
    monkeypatch.setattr(config, "payment_email", "theastralx@gmail.com")
    monkeypatch.setattr(config, "pro_price_usd", 1)
    monkeypatch.setattr(config, "unlimited_price_usd", 20)
    monkeypatch.setattr(config, "payment_qr_path", None)
    await init_db()
    return test_db

def create_mock_update(user_id: int, message_text: str):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.message.message_id = 100
    update.message.text = message_text

    proc_msg = MagicMock()
    proc_msg.message_id = 200
    update.message.reply_text = AsyncMock(return_value=proc_msg)
    update.message.reply_photo = AsyncMock()
    return update

def create_mock_context():
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return context

@pytest.mark.asyncio
async def test_format_payment_instructions_pro():
    text = format_payment_instructions(Plan.PRO, USER_ID_FREE)
    assert "You've reached your Free download limit." in text
    assert "Reset: in 24h 0m" in text
    assert "Pro: $1/month" in text
    assert "500 downloads per 24h" in text
    assert "Payment Email: theastralx@gmail.com" in text
    assert "Amount: $1" in text
    assert f"Telegram ID: {USER_ID_FREE}" in text

@pytest.mark.asyncio
async def test_format_payment_instructions_unlimited():
    text = format_payment_instructions(Plan.UNLIMITED, USER_ID_PRO)
    assert "You've reached your Pro download limit." in text
    assert "Reset: in 24h 0m" in text
    assert "Unlimited: $20 one-time" in text
    assert "Permanent access" in text
    assert "Payment Email: theastralx@gmail.com" in text
    assert "Amount: $20" in text
    assert f"Telegram ID: {USER_ID_PRO}" in text

def test_format_payment_instructions_unsupported():
    with pytest.raises(ValueError):
        format_payment_instructions("INVALID_PLAN", 123)

@pytest.mark.asyncio
async def test_free_user_below_limit_submits_normally(monkeypatch):
    monkeypatch.setattr("src.queue_manager.enqueue_job", AsyncMock())
    update = create_mock_update(USER_ID_FREE, "https://www.instagram.com/reel/C12345678/")
    context = create_mock_context()

    await handle_message(update, context)

    update.message.reply_text.assert_called_with("Processing your video...")

@pytest.mark.asyncio
async def test_free_user_at_limit_receives_upgrade_prompt(monkeypatch):
    now = datetime.now(timezone.utc)
    for _ in range(100):
        await record_successful_delivery(USER_ID_FREE, now=now)

    update = create_mock_update(USER_ID_FREE, "https://www.instagram.com/reel/C12345678/")
    context = create_mock_context()

    await handle_message(update, context)

    reply_text = update.message.reply_text.call_args[0][0]
    assert "You've reached your Free download limit." in reply_text
    assert "Pro: $1/month" in reply_text
    assert "Payment Email: theastralx@gmail.com" in reply_text
    assert f"Telegram ID: {USER_ID_FREE}" in reply_text

    # Verify NO job was created in DB
    pending_count = await get_pending_jobs_count(USER_ID_FREE)
    assert pending_count == 0

@pytest.mark.asyncio
async def test_pro_user_at_limit_receives_unlimited_upgrade_prompt(monkeypatch):
    await grant_pro(USER_ID_PRO, days=30)
    now = datetime.now(timezone.utc)
    for _ in range(500):
        await record_successful_delivery(USER_ID_PRO, now=now)

    update = create_mock_update(USER_ID_PRO, "https://www.instagram.com/reel/C12345678/")
    context = create_mock_context()

    await handle_message(update, context)

    reply_text = update.message.reply_text.call_args[0][0]
    assert "You've reached your Pro download limit." in reply_text
    assert "Unlimited: $20 one-time" in reply_text
    assert "Permanent access" in reply_text
    assert f"Telegram ID: {USER_ID_PRO}" in reply_text
    assert "Pro: $1/month" not in reply_text

    pending_count = await get_pending_jobs_count(USER_ID_PRO)
    assert pending_count == 0

@pytest.mark.asyncio
async def test_unlimited_user_never_receives_upgrade_prompt(monkeypatch):
    monkeypatch.setattr("src.queue_manager.enqueue_job", AsyncMock())
    await grant_unlimited(USER_ID_UNLIMITED)
    now = datetime.now(timezone.utc)
    for _ in range(600):
        await record_successful_delivery(USER_ID_UNLIMITED, now=now)

    update = create_mock_update(USER_ID_UNLIMITED, "https://www.instagram.com/reel/C12345678/")
    context = create_mock_context()

    await handle_message(update, context)

    update.message.reply_text.assert_called_with("Processing your video...")

@pytest.mark.asyncio
async def test_payment_instructions_do_not_grant_membership():
    now = datetime.now(timezone.utc)
    for _ in range(100):
        await record_successful_delivery(USER_ID_FREE, now=now)

    update = create_mock_update(USER_ID_FREE, "https://www.instagram.com/reel/C12345678/")
    context = create_mock_context()

    await handle_message(update, context)

    mem = await get_user_membership(USER_ID_FREE)
    assert mem.effective_plan == Plan.FREE

@pytest.mark.asyncio
async def test_qr_code_sending_local_file(tmp_path, monkeypatch):
    qr_file = tmp_path / "test_qr.png"
    qr_file.write_bytes(b"fake_png_data")
    monkeypatch.setattr(config, "payment_qr_path", str(qr_file))

    update = create_mock_update(USER_ID_FREE, "")
    context = create_mock_context()

    # Manually trigger send_upgrade_prompt
    now = datetime.now(timezone.utc)
    for _ in range(100):
        await record_successful_delivery(USER_ID_FREE, now=now)

    await send_upgrade_prompt(update, context, USER_ID_FREE, now=now)

    assert update.message.reply_photo.called is True
    caption = update.message.reply_photo.call_args[1]["caption"]
    assert "You've reached your Free download limit." in caption

@pytest.mark.asyncio
async def test_qr_code_missing_file_graceful_fallback(monkeypatch):
    monkeypatch.setattr(config, "payment_qr_path", "/nonexistent/path/qr.png")

    update = create_mock_update(USER_ID_FREE, "")
    context = create_mock_context()

    now = datetime.now(timezone.utc)
    for _ in range(100):
        await record_successful_delivery(USER_ID_FREE, now=now)

    await send_upgrade_prompt(update, context, USER_ID_FREE, now=now)

    # Should fall back gracefully to reply_text without crashing
    assert update.message.reply_text.called is True
    text = update.message.reply_text.call_args[0][0]
    assert "You've reached your Free download limit." in text

def test_validate_payment_config(monkeypatch):
    monkeypatch.setattr(config, "payment_email", "theastralx@gmail.com")
    monkeypatch.setattr(config, "pro_price_usd", 1)
    monkeypatch.setattr(config, "unlimited_price_usd", 20)
    assert config.validate_payment_config() is True

    monkeypatch.setattr(config, "payment_email", "invalid_email_string")
    assert config.validate_payment_config() is False
