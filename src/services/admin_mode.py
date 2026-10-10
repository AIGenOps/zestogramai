import logging
from typing import Optional, Dict, Any, Tuple
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from src.config import config
from src.db import (
    get_admin_mode_user,
    upsert_admin_mode_user,
    update_admin_mode_status,
    update_admin_mode_current_mode,
    get_pending_submission_jobs,
    cancel_pending_submission_jobs,
    activate_pending_submission_jobs,
)
from src.queue_manager import enqueue_job

logger = logging.getLogger(__name__)

async def get_admin_mode_state(user_id: int) -> Dict[str, Any]:
    row = await get_admin_mode_user(user_id)
    if not row:
        return {
            "user_id": user_id,
            "status": "NONE",
            "current_mode": "NORMAL",
            "topic_id": None,
            "user_name": None
        }
    return {
        "user_id": user_id,
        "status": row.get("status") or "NONE",
        "current_mode": row.get("current_mode") or "NORMAL",
        "topic_id": row.get("topic_id"),
        "user_name": row.get("user_name")
    }

async def set_mode_to_normal(user_id: int) -> Tuple[bool, str]:
    state = await get_admin_mode_state(user_id)
    await upsert_admin_mode_user(
        user_id=user_id,
        status=state.get("status", "NONE"),
        current_mode="NORMAL",
        topic_id=state.get("topic_id"),
        user_name=state.get("user_name")
    )
    return True, "Switched to Normal Download Mode."

async def request_or_switch_to_admin_mode(
    bot,
    user_id: int,
    user_info: Dict[str, Any]
) -> Tuple[bool, str]:
    state = await get_admin_mode_state(user_id)
    status = state.get("status")

    if status == "APPROVED":
        await update_admin_mode_current_mode(user_id, "ADMIN")
        return True, "Switched to Send to Admin Mode. All reels you send will now be forwarded to the admin."

    if status == "PENDING":
        return True, "⏳ Your submission request is currently pending admin approval. Any reels you send will be queued."

    # Status is NONE or DISAPPROVED (initial request or re-apply)
    if not config.admin_forum_group_id:
        logger.error("ADMIN_FORUM_GROUP_ID is not configured.")
        return False, "Admin forum group is not configured on this bot."

    first_name = user_info.get("first_name", "")
    last_name = user_info.get("last_name", "")
    username = user_info.get("username")
    full_name = f"{first_name} {last_name}".strip() or (f"@{username}" if username else f"User {user_id}")
    topic_title = f"{full_name} | {user_id}"[:128]

    try:
        topic = await bot.create_forum_topic(
            chat_id=config.admin_forum_group_id,
            name=topic_title
        )
        topic_id = topic.message_thread_id
    except Exception as e:
        logger.error(f"Failed to create forum topic for user {user_id}: {e}")
        return False, f"Could not create topic in admin group: {e}"

    approval_text = (
        f"👤 <b>New Submission Request</b>\n\n"
        f"<b>User:</b> {full_name}\n"
        f"<b>Username:</b> @{username if username else 'N/A'}\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n\n"
        f"Requested permission to send reels to admin."
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub:{user_id}"),
            InlineKeyboardButton("❌ Disapprove", callback_data=f"disapprove_sub:{user_id}")
        ]
    ])

    try:
        await bot.send_message(
            chat_id=config.admin_forum_group_id,
            message_thread_id=topic_id,
            text=approval_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Failed to send approval message into topic {topic_id}: {e}")

    await upsert_admin_mode_user(
        user_id=user_id,
        status="PENDING",
        current_mode="ADMIN",
        topic_id=topic_id,
        user_name=full_name
    )

    return True, "⏳ Please wait for confirmation. A submission request has been sent to the admin. Any reels you send will be queued until approved."

async def approve_user_submission(
    bot,
    user_id: int,
    admin_name: str
) -> Tuple[bool, str]:
    state = await get_admin_mode_state(user_id)
    topic_id = state.get("topic_id")

    await update_admin_mode_status(
        user_id=user_id,
        status="APPROVED",
        current_mode="ADMIN"
    )

    # Notify user in DM
    try:
        await bot.send_message(
            chat_id=user_id,
            text="🎉 Your request to send reels to admin has been approved! Your queued and upcoming reels will be sent directly to admin."
        )
    except Exception as e:
        logger.warning(f"Failed to notify user {user_id} of approval: {e}")

    # Activate and enqueue pending submission jobs
    if topic_id and config.admin_forum_group_id:
        activated_jobs = await activate_pending_submission_jobs(
            user_id=user_id,
            target_chat_id=config.admin_forum_group_id,
            message_thread_id=topic_id
        )
        for job in activated_jobs:
            try:
                await enqueue_job({
                    'job_id': job['id'],
                    'user_id': user_id,
                    'chat_id': job['chat_id'],
                    'url': job['url'],
                    'message_id': job.get('message_id'),
                    'original_message_id': job.get('original_message_id'),
                    'target_chat_id': config.admin_forum_group_id,
                    'message_thread_id': topic_id,
                    'is_batch': False
                })
            except Exception as e:
                logger.error(f"Failed to enqueue activated job {job['id']}: {e}")

    return True, f"Approved submission request for user {user_id}."

async def disapprove_user_submission(
    bot,
    user_id: int,
    admin_name: str
) -> Tuple[bool, str]:
    state = await get_admin_mode_state(user_id)
    topic_id = state.get("topic_id")

    # Update DB state
    await update_admin_mode_status(
        user_id=user_id,
        status="DISAPPROVED",
        current_mode="NORMAL",
        clear_topic=True
    )

    # Cancel pending submission jobs
    await cancel_pending_submission_jobs(user_id)

    # Notify user in DM with exact requested text
    try:
        await bot.send_message(
            chat_id=user_id,
            text="Your submission request was declined. Switching back to normal download mode."
        )
    except Exception as e:
        logger.warning(f"Failed to notify user {user_id} of decline: {e}")

    # Delete forum topic in supergroup
    if topic_id and config.admin_forum_group_id:
        try:
            await bot.delete_forum_topic(
                chat_id=config.admin_forum_group_id,
                message_thread_id=topic_id
            )
        except Exception as e:
            logger.warning(f"Failed to delete forum topic {topic_id}: {e}")

    return True, f"Disapproved submission request and deleted topic for user {user_id}."
