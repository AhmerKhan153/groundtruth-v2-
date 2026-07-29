"""Telegram send helpers.

All human-in-the-loop state lives in MongoDB; callback_data carries the string
draft id (never an in-memory index), so buttons keep working across restarts and
for many concurrent drafts.
"""

from typing import Dict, List

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from src.config import BOT_TOKEN, CHAT_ID
except ImportError:  # pragma: no cover - fallback for direct script execution
    from config import BOT_TOKEN, CHAT_ID

_bot = None


def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not BOT_TOKEN:
            raise RuntimeError(
                "Telegram bot token is not configured. Set TELEGRAM_BOT_TOKEN or BOT_TOKEN."
            )
        _bot = Bot(BOT_TOKEN)
    return _bot


async def send_text_message(content: str, reply_markup=None):
    if not CHAT_ID:
        raise RuntimeError(
            "Telegram chat ID is not configured. Set TELEGRAM_CHAT_ID or CHAT_ID."
        )
    await _get_bot().send_message(
        chat_id=CHAT_ID, text=content, reply_markup=reply_markup
    )


async def send_story_selection(stories: List[Dict]) -> None:
    """Show ranked stories; each button carries the story's Mongo id.

    Each story dict must contain: id, title, score, comments (optional).
    """
    if not stories:
        await send_text_message("No stories were sourced this cycle.")
        return

    lines = ["Pick a story to draft a LinkedIn post:\n"]
    buttons = []
    row = []
    for index, story in enumerate(stories, start=1):
        title = story.get("title") or "Untitled"
        score = story.get("score", 0)
        comments = story.get("comments", 0)
        lines.append(f"{index}. {title}  ({score}▲ {comments}💬)")
        row.append(
            InlineKeyboardButton(str(index), callback_data=f"pick:{story['id']}")
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await send_text_message(
        content="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def send_draft(draft_id: str, content: str, source_url: str = "") -> None:
    """Send a generated draft with approve / reject / rewrite controls.

    The source URL rides along with every draft so the claims in the post can be
    checked against the article before approving it.
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{draft_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{draft_id}"),
        ],
        [InlineKeyboardButton("✍️ Rewrite", callback_data=f"rewrite:{draft_id}")],
    ]
    body = f"{content}\n\n— — —\n🔗 Source (verify before approving):\n{source_url}" if source_url else content
    await send_text_message(
        content=body, reply_markup=InlineKeyboardMarkup(keyboard)
    )
