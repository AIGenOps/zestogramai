import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import src.db as db_module
from src.config import config
from src.db import init_db, save_user_db, add_job, add_admin_db, is_admin_db
from src.services.membership import get_user_membership, grant_pro, grant_unlimited, Plan
from src.handlers.admin import (
    is_owner,
    is_admin,
    stats_command,
    user_command,
    grantpro_command,
    grantunlimited_command,
    revoke_command,
    addadmin_command,
    removeadmin_command,
    admins_command
)

OWNER_ID = 999111
ADMIN_ID = 999222
REGULAR_USER_ID = 999333

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_admin.db")
    monkeypatch.setenv("DB_PATH", test_db)
    monkeypatch.setattr(config, "owner_telegram_id", OWNER_ID)
    monkeypatch.setattr(config, "admin_user_ids", "")
    await init_db()
    return test_db

def create_mock_update(user_id: int):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = AsyncMock()
    return update

def create_mock_context(args=None):
    context = MagicMock()
    context.args = args if args is not None else []
    return context

@pytest.mark.asyncio
async def test_authorization_checks():
    assert is_owner(OWNER_ID) is True
    assert is_owner(ADMIN_ID) is False
    assert is_owner(REGULAR_USER_ID) is False

    assert await is_admin(OWNER_ID) is True
    assert await is_admin(ADMIN_ID) is False
    assert await is_admin(REGULAR_USER_ID) is False

    await add_admin_db(ADMIN_ID)
    assert await is_admin(ADMIN_ID) is True

@pytest.mark.asyncio
async def test_unauthorized_callers():
    update = create_mock_update(REGULAR_USER_ID)
    context = create_mock_context()

    admin_commands = [
        stats_command,
        user_command,
        grantpro_command,
        grantunlimited_command,
        revoke_command,
        addadmin_command,
        removeadmin_command,
        admins_command
    ]

    for cmd in admin_commands:
        update.message.reply_text.reset_mock()
        await cmd(update, context)
        update.message.reply_text.assert_called_once_with("You are not authorized to use this command.")

@pytest.mark.asyncio
async def test_admin_cannot_use_owner_commands():
    await add_admin_db(ADMIN_ID)
    update = create_mock_update(ADMIN_ID)
    context = create_mock_context([str(REGULAR_USER_ID)])

    owner_only_cmds = [addadmin_command, removeadmin_command, admins_command]
    for cmd in owner_only_cmds:
        update.message.reply_text.reset_mock()
        await cmd(update, context)
        update.message.reply_text.assert_called_once_with("You are not authorized to use this command.")

@pytest.mark.asyncio
async def test_stats_command():
    now_str = datetime.now(timezone.utc).isoformat()
    await save_user_db(101, membership_type="FREE")
    await save_user_db(102, membership_type="PRO", pro_expires_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    await save_user_db(103, membership_type="UNLIMITED", is_unlimited=True)

    job1 = await add_job(101, 101, 1, "https://instagram.com/reel/1")
    await db_module.update_job_status(job1, "success")
    job2 = await add_job(102, 102, 2, "https://instagram.com/reel/2")
    await db_module.update_job_status(job2, "failed")
    job3 = await add_job(103, 103, 3, "https://instagram.com/reel/3")
    await db_module.update_job_status(job3, "downloading")
    job4 = await add_job(101, 101, 4, "https://instagram.com/reel/4")

    update = create_mock_update(OWNER_ID)
    context = create_mock_context()
    await stats_command(update, context)

    reply_text = update.message.reply_text.call_args[0][0]
    assert "Users: 3" in reply_text
    assert "Free: 1" in reply_text
    assert "Pro: 1" in reply_text
    assert "Unlimited: 1" in reply_text
    assert "Successful downloads: 1" in reply_text
    assert "Currently processing: 1" in reply_text
    assert "Queued: 1" in reply_text
    assert "Failed: 1" in reply_text

@pytest.mark.asyncio
async def test_user_command_flow():
    update = create_mock_update(OWNER_ID)

    # 1. Unknown user
    context = create_mock_context(["999999"])
    await user_command(update, context)
    update.message.reply_text.assert_called_with("User not found.")

    # 2. Existing Free user
    await save_user_db(101, membership_type="FREE")
    context = create_mock_context(["101"])
    await user_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "User ID: 101" in reply
    assert "Plan: Free" in reply

    # 3. Existing Pro user
    pro_exp = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    await save_user_db(102, membership_type="PRO", pro_expires_at=pro_exp)
    context = create_mock_context(["102"])
    await user_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "User ID: 102" in reply
    assert "Plan: Pro" in reply
    assert "Pro expires:" in reply

    # 4. Unlimited user
    await save_user_db(103, membership_type="UNLIMITED", is_unlimited=True)
    context = create_mock_context(["103"])
    await user_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "User ID: 103" in reply
    assert "Plan: Unlimited" in reply

@pytest.mark.asyncio
async def test_grantpro_command_scenarios():
    update = create_mock_update(OWNER_ID)
    now = datetime.now(timezone.utc)

    # 1. New user -> Pro 30 days
    context = create_mock_context(["201"])
    await grantpro_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "Pro granted to 201." in reply
    assert "Pro expires:" in reply
    mem = await get_user_membership(201)
    assert mem.effective_plan == Plan.PRO

    # 2. Active Pro -> extend by 30 days
    context = create_mock_context(["201"])
    await grantpro_command(update, context)
    mem2 = await get_user_membership(201)
    exp_diff = (mem2.pro_expires_at - now).total_seconds()
    assert 59 * 86400 < exp_diff < 61 * 86400

    # 3. Unlimited user -> return higher priority message
    await grant_unlimited(202)
    context = create_mock_context(["202"])
    await grantpro_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "already has higher priority" in reply or "Unlimited" in reply
    mem_unlim = await get_user_membership(202)
    assert mem_unlim.effective_plan == Plan.UNLIMITED

@pytest.mark.asyncio
async def test_grantunlimited_command_scenarios():
    update = create_mock_update(OWNER_ID)

    # 1. Free -> Unlimited
    context = create_mock_context(["301"])
    await grantunlimited_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "Unlimited granted to 301." in reply
    mem = await get_user_membership(301)
    assert mem.effective_plan == Plan.UNLIMITED

    # 2. Pro -> Unlimited (discard pro expiry)
    await grant_pro(302, days=15)
    context = create_mock_context(["302"])
    await grantunlimited_command(update, context)
    mem_pro_to_unlim = await get_user_membership(302)
    assert mem_pro_to_unlim.effective_plan == Plan.UNLIMITED
    assert mem_pro_to_unlim.pro_expires_at is None

@pytest.mark.asyncio
async def test_revoke_command_scenarios():
    update = create_mock_update(OWNER_ID)

    # Pro -> Free
    await grant_pro(401, days=30)
    context = create_mock_context(["401"])
    await revoke_command(update, context)
    mem1 = await get_user_membership(401)
    assert mem1.effective_plan == Plan.FREE

    # Unlimited -> Free
    await grant_unlimited(402)
    context = create_mock_context(["402"])
    await revoke_command(update, context)
    mem2 = await get_user_membership(402)
    assert mem2.effective_plan == Plan.FREE

    # Historical job preserved
    j_id = await add_job(401, 401, 1, "https://instagram.com/reel/test")
    await db_module.update_job_status(j_id, "success")
    job = await db_module.get_job(j_id)
    assert job["status"] == "success"

@pytest.mark.asyncio
async def test_admin_management_flow():
    update = create_mock_update(OWNER_ID)

    # 1. Initially no additional admins
    context = create_mock_context()
    await admins_command(update, context)
    update.message.reply_text.assert_called_with("No additional admins configured.")

    # 2. Add admin
    context = create_mock_context([str(ADMIN_ID)])
    await addadmin_command(update, context)
    update.message.reply_text.assert_called_with(f"Admin added: {ADMIN_ID}.")
    assert await is_admin(ADMIN_ID) is True

    # 3. Duplicate add
    context = create_mock_context([str(ADMIN_ID)])
    await addadmin_command(update, context)
    update.message.reply_text.assert_called_with("User is already an admin.")

    # 4. List admins
    context = create_mock_context()
    await admins_command(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert "Admins:" in reply
    assert str(ADMIN_ID) in reply

    # 5. Remove owner attempt
    context = create_mock_context([str(OWNER_ID)])
    await removeadmin_command(update, context)
    update.message.reply_text.assert_called_with("Cannot remove owner.")

    # 6. Remove non-admin
    context = create_mock_context(["999999"])
    await removeadmin_command(update, context)
    update.message.reply_text.assert_called_with("User is not an admin.")

    # 7. Remove admin
    context = create_mock_context([str(ADMIN_ID)])
    await removeadmin_command(update, context)
    update.message.reply_text.assert_called_with(f"Admin removed: {ADMIN_ID}.")
    assert await is_admin(ADMIN_ID) is False

@pytest.mark.asyncio
async def test_argument_validation():
    update = create_mock_update(OWNER_ID)

    commands_with_usage = [
        (user_command, "Usage: /user <telegram_id>"),
        (grantpro_command, "Usage: /grantpro <telegram_id>"),
        (grantunlimited_command, "Usage: /grantunlimited <telegram_id>"),
        (revoke_command, "Usage: /revoke <telegram_id>"),
        (addadmin_command, "Usage: /addadmin <telegram_id>"),
        (removeadmin_command, "Usage: /removeadmin <telegram_id>"),
    ]

    for cmd, usage_str in commands_with_usage:
        # Missing arg
        update.message.reply_text.reset_mock()
        await cmd(update, create_mock_context([]))
        update.message.reply_text.assert_called_once_with(usage_str)

        # Malformed arg (text)
        update.message.reply_text.reset_mock()
        await cmd(update, create_mock_context(["not_a_number"]))
        update.message.reply_text.assert_called_once_with(usage_str)
