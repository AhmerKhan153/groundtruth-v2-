"""Telegram callback dispatch — the human-in-the-loop orchestrator.

Callback data is "<action>:<draft_id>". Each action does one chunk of work using
the Mongo record identified by draft_id, then stops:

    pick     -> run fetch->write subgraph, store draft, send it for approval
    approve  -> mark approved (publish job will post it)
    reject   -> mark rejected
    rewrite  -> regenerate the draft from a different angle, resend
"""

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from workflow.content_graph import generate_draft, ArticleUnavailable
from integration.telegram_bot import send_draft, send_text_message
from repository import draft_repository
from config import STATUS_APPROVED, STATUS_REJECTED


async def _draft_and_send(draft_id: str, title: str, url: str, is_rewrite: bool) -> None:
    """Generate a draft off the event loop, persist it, and send it for review."""
    try:
        draft = await asyncio.to_thread(generate_draft, title, url, is_rewrite)
    except ArticleUnavailable as exc:
        # No article text means any draft would be invented. Say so and stop.
        await send_text_message(f"⚠️ Skipped “{title}”: {exc}\n{url}")
        return

    await asyncio.to_thread(draft_repository.attach_draft, draft_id, draft)
    await send_draft(draft_id, draft, source_url=url or "")


async def _mark(query, status: str) -> None:
    """Keep the draft (and its source URL) on screen; append the outcome.

    Passing no reply_markup clears the inline keyboard, so a draft can't be
    acted on twice, while the post itself stays readable in the chat history.
    """
    original = query.message.text or ""
    await query.edit_message_text(f"{original}\n\n— — —\n{status}")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.data or ":" not in query.data:
        return

    action, draft_id = query.data.split(":", 1)
    record = draft_repository.get(draft_id)
    if record is None:
        await query.edit_message_text("This item is no longer available.")
        return

    if action == "pick":
        title = record.get("title") or ""
        await query.edit_message_text(f"✅ Selected: {title}\n\nWriting a draft...")
        await _draft_and_send(draft_id, title, record.get("url"), is_rewrite=False)

    elif action == "approve":
        draft_repository.set_status(draft_id, STATUS_APPROVED)
        await _mark(query, "✅ Approved — queued for LinkedIn.")

    elif action == "reject":
        draft_repository.set_status(draft_id, STATUS_REJECTED)
        await _mark(query, "❌ Rejected.")

    elif action == "rewrite":
        await _mark(query, "✍️ Rewriting from a different angle...")
        await _draft_and_send(
            draft_id, record.get("title") or "", record.get("url"), is_rewrite=True
        )
