"""A thin Telegram Bot API client: the handful of calls this app makes.

Plain HTTPS instead of python-telegram-bot: that library is built around a
long-running Application, which doesn't fit a request handler that lives for one
webhook call. Everything is plain text (no parse_mode), so drafts never need
escaping and are exactly what you copy into LinkedIn.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from groundtruth.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 15
# Telegram rejects longer messages outright.
MAX_TEXT = 4096

Button = Tuple[str, str]  # (label, callback_data); callback_data is at most 64 bytes


class TelegramError(RuntimeError):
    pass


_session = requests.Session()


def call(method: str, **params: Any) -> Any:
    if not TELEGRAM_BOT_TOKEN:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set.")
    try:
        response = _session.post(
            _API.format(token=TELEGRAM_BOT_TOKEN, method=method),
            json=params,
            # getUpdates long-polls for `timeout` seconds; wait that long plus margin.
            timeout=_TIMEOUT + int(params.get("timeout") or 0),
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"{method} failed: {exc}") from exc
    if not body.get("ok"):
        raise TelegramError(f"{method} failed: {body.get('description', response.status_code)}")
    return body.get("result")


def keyboard(rows: Sequence[Sequence[Button]]) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in rows
        ]
    }


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def send_message(text: str, buttons: Optional[List[List[Button]]] = None) -> int:
    """Send to TELEGRAM_CHAT_ID; returns the message id for later edits."""
    if not TELEGRAM_CHAT_ID:
        raise TelegramError("TELEGRAM_CHAT_ID is not set.")
    params: Dict[str, Any] = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": _clip(text),
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        params["reply_markup"] = keyboard(buttons)
    return call("sendMessage", **params)["message_id"]


def edit_message(message_id: int, text: str, buttons: Optional[List[List[Button]]] = None) -> None:
    """Replace a message's text. Omitting `buttons` removes its keyboard, so a
    decided draft can't be acted on twice."""
    params: Dict[str, Any] = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": _clip(text),
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        params["reply_markup"] = keyboard(buttons)
    try:
        call("editMessageText", **params)
    except TelegramError as exc:
        # A resent update can try to set the text it already has; that's fine.
        if "message is not modified" not in str(exc):
            raise


def answer_callback(callback_id: str, text: Optional[str] = None) -> None:
    """Stop the button's loading spinner; `text` shows as a brief toast."""
    params: Dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    try:
        call("answerCallbackQuery", **params)
    except TelegramError:
        # Callback ids expire after a while; a late answer must not abort the work.
        pass
