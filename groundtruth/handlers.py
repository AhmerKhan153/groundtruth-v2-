"""The two entry points behind the HTTP endpoints: the source job and button taps.

Every tap starts with an atomic claim in the store. A claim that misses means the
work was already done or is in progress (Telegram resent the update, a double tap,
a stale button), so the handler answers with a toast and stops. That is the whole
duplicate-safety story; nothing here keeps state between requests.

Button callback data is "<action>:<draft id>":

    p  pick     sourced/failed -> drafting -> drafted   (fetch, extract, write)
    a  approve  drafted -> approved                     (terminal; copy-ready message)
    r  reject   drafted -> rejected
    w  rewrite  drafted -> drafting -> drafted          (one model call, from stored brief)
"""

import logging
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from groundtruth import pipeline, store, telegram
from groundtruth.config import STORIES_PER_RUN, TELEGRAM_CHAT_ID
from groundtruth.sources import hackernews

log = logging.getLogger(__name__)

_RULE = "— — —"


# --- source job ------------------------------------------------------------------

def source_job(force: bool = False) -> Dict[str, Any]:
    """Source fresh stories and send the pick list. Raises on failure (-> 500 -> the
    scheduler retries); the run is only recorded once the list is delivered."""
    if not store.source_due(force):
        return {"status": "skipped"}

    # Stories from an earlier attempt whose pick list never went out come first;
    # new ones only fill the remaining room, so a retry doesn't grow the list.
    room = STORIES_PER_RUN - len(store.unannounced())
    if room > 0:
        candidates = hackernews.fetch_candidates()
        known = store.known_hn_ids(c["hn_id"] for c in candidates)
        store.insert_sourced([c for c in candidates if c["hn_id"] not in known][:room])

    pending = store.unannounced()
    if pending:
        telegram.send_message(_pick_list_text(pending), buttons=_pick_buttons(pending))
        store.mark_announced(d["id"] for d in pending)
    else:
        telegram.send_message("No new stories this time. Everything on the front page was already shown.")
    store.record_source_run()
    return {"status": "sent", "count": len(pending)}


def _pick_list_text(stories) -> str:
    lines = ["Pick a story to draft:\n"]
    for i, s in enumerate(stories, 1):
        domain = urlparse(s.get("url") or "").netloc.removeprefix("www.")
        lines.append(f"{i}. {s['title']}\n   {s.get('score', 0)}▲ {s.get('comments', 0)}💬 · {domain}")
    return "\n".join(lines)


def _pick_buttons(stories):
    buttons = [(str(i), f"p:{s['id']}") for i, s in enumerate(stories, 1)]
    return [buttons[i : i + 4] for i in range(0, len(buttons), 4)]


# --- button taps ---------------------------------------------------------------------

def handle_update(update: Dict[str, Any]) -> None:
    """Dispatch one Telegram update. Never raises: the webhook must answer 200, or
    Telegram resends the update and the work repeats."""
    query = update.get("callback_query")
    if not query:
        return
    chat_id = ((query.get("message") or {}).get("chat") or {}).get("id")
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        log.warning("ignored callback from chat %s", chat_id)
        return

    action, _, draft_id = (query.get("data") or "").partition(":")
    handler = _ACTIONS.get(action)
    if handler is None:
        telegram.answer_callback(query["id"])
        return
    try:
        handler(query["id"], draft_id)
    except Exception as exc:  # report, don't crash: see docstring
        log.exception("callback %s failed", query.get("data"))
        try:
            telegram.send_message(f"⚠️ Something went wrong ({type(exc).__name__}: {exc}). Tap the button again.")
        except telegram.TelegramError:
            log.exception("could not report the failure to Telegram")


def _pick(callback_id: str, draft_id: str) -> None:
    record = store.start_pick(draft_id)
    if record is None:
        return telegram.answer_callback(callback_id, _why_not(draft_id))
    telegram.answer_callback(callback_id, "Drafting…")

    title, url = record["title"], record["url"]
    msg_id = _show(record, f"✍️ Drafting: {title}")
    try:
        brief, draft = pipeline.draft_from_url(title, url)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, pipeline.DraftError) else f"Unexpected error: {type(exc).__name__}"
        store.fail_draft(draft_id, reason)
        telegram.edit_message(
            msg_id, f"⚠️ Skipped: {title}\n{reason}\n{url}\n\nTap its number in the list to try again."
        )
        return

    if store.finish_draft(draft_id, brief, draft) is not None:
        telegram.edit_message(msg_id, _draft_text(title, url, draft), buttons=_draft_buttons(draft_id))


def _approve(callback_id: str, draft_id: str) -> None:
    record = store.approve(draft_id)
    if record is None:
        return telegram.answer_callback(callback_id, _why_not(draft_id))
    telegram.answer_callback(callback_id, "Approved")
    telegram.edit_message(record["msg_id"], _draft_text(record["title"], record["url"], record["draft"], "✅ Approved"))
    # The post alone, so long-press -> Copy is exactly what goes into LinkedIn.
    telegram.send_message(record["draft"])


def _reject(callback_id: str, draft_id: str) -> None:
    record = store.reject(draft_id)
    if record is None:
        return telegram.answer_callback(callback_id, _why_not(draft_id))
    telegram.answer_callback(callback_id, "Rejected")
    telegram.edit_message(record["msg_id"], _draft_text(record["title"], record["url"], record["draft"], "❌ Rejected"))


def _rewrite(callback_id: str, draft_id: str) -> None:
    record = store.start_rewrite(draft_id)
    if record is None:
        return telegram.answer_callback(callback_id, _why_not(draft_id))
    telegram.answer_callback(callback_id, "Rewriting…")

    title, url = record["title"], record["url"]
    brief, previous = record.get("brief"), record.get("draft")
    msg_id = _show(record, _draft_text(title, url, previous or "", "✍️ Rewriting…"))
    try:
        if brief:
            draft = pipeline.write_post(title, brief, previous_draft=previous)
        else:  # reclaimed a pick that died before its first draft
            brief, draft = pipeline.draft_from_url(title, url)
    except Exception as exc:
        if not brief or not previous:
            store.fail_draft(draft_id, str(exc))
            telegram.edit_message(msg_id, f"⚠️ Skipped: {title}\n{exc}\n{url}")
            return
        # The previous draft is still good: put it back with its buttons.
        store.finish_draft(draft_id, brief, previous)
        telegram.edit_message(
            msg_id,
            _draft_text(title, url, previous, f"⚠️ Rewrite failed: {exc}"),
            buttons=_draft_buttons(draft_id),
        )
        return

    if store.finish_draft(draft_id, brief, draft) is not None:
        telegram.edit_message(msg_id, _draft_text(title, url, draft), buttons=_draft_buttons(draft_id))


_ACTIONS: Dict[str, Callable[[str, str], None]] = {
    "p": _pick,
    "a": _approve,
    "r": _reject,
    "w": _rewrite,
}


# --- helpers ---------------------------------------------------------------------------

def _show(record: Dict[str, Any], text: str) -> int:
    """Put `text` in the story's own message: edit it if one exists, else send one.

    One message per story, edited in place through drafting, rewrites and the
    decision, keeps the chat readable.
    """
    msg_id: Optional[int] = record.get("msg_id")
    if msg_id:
        try:
            telegram.edit_message(msg_id, text)
            return msg_id
        except telegram.TelegramError:
            log.warning("could not edit message %s; sending a new one", msg_id)
    msg_id = telegram.send_message(text)
    store.set_msg_id(record["id"], msg_id)
    return msg_id


def _draft_text(title: str, url: str, draft: str, status: Optional[str] = None) -> str:
    # Clip the draft, not the footer, so the source link always survives.
    footer = f"\n\n{_RULE}\n🔗 {title}\n{url}" + (f"\n\n{status}" if status else "")
    room = telegram.MAX_TEXT - len(footer)
    body = draft if len(draft) <= room else draft[: room - 1] + "…"
    return body + footer


def _draft_buttons(draft_id: str):
    return [
        [("✅ Approve", f"a:{draft_id}"), ("❌ Reject", f"r:{draft_id}")],
        [("✍️ Rewrite", f"w:{draft_id}")],
    ]


def _why_not(draft_id: str) -> str:
    """Toast for a tap whose claim missed."""
    record = store.get(draft_id)
    if record is None:
        return "This story has expired."
    if record["status"] == store.DRAFTING:
        return "Already working on it…"
    return f"Already {record['status']}."
