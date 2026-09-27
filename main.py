"""Groundtruth HTTP service: three endpoints, no long-running work.

    POST /jobs/source   Cloud Scheduler, daily; X-Job-Secret. Sources stories and
                        sends the pick list (skips itself if the last run was <44h ago;
                        ?force=true overrides).
    POST /telegram      Telegram webhook; X-Telegram-Bot-Api-Secret-Token. One
                        button tap per request.
    GET  /healthz       Liveness; touches no dependencies.

Handlers are plain `def`, so FastAPI runs them in its thread pool: the blocking
calls (Mongo, DeepSeek, Telegram) never stall the event loop, and a slow draft
doesn't block an approve arriving meanwhile.

Local run:  uvicorn main:app --reload
"""

import hmac
import json
import logging
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Header, HTTPException

from groundtruth import handlers
from groundtruth.config import JOB_SECRET, TELEGRAM_WEBHOOK_SECRET


class _JsonFormatter(logging.Formatter):
    """One JSON object per line: Cloud Logging turns these into structured entries
    with the right severity."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.WARNING, handlers=[_handler])
logging.getLogger("groundtruth").setLevel(logging.INFO)

app = FastAPI(title="groundtruth", docs_url=None, redoc_url=None, openapi_url=None)


def _authorized(presented: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time compare; an unset secret refuses everything (fail closed)."""
    return bool(expected) and hmac.compare_digest((presented or "").encode(), expected.encode())


@app.get("/healthz")
def healthz() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/jobs/source")
def source(
    force: bool = False,
    x_job_secret: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if not _authorized(x_job_secret, JOB_SECRET):
        raise HTTPException(status_code=401)
    # Errors propagate as 500 on purpose: Cloud Scheduler retries, and the job
    # only records a run after the pick list was delivered.
    return handlers.source_job(force=force)


@app.post("/telegram")
def telegram_webhook(
    update: Dict[str, Any] = Body(...),
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> Dict[str, bool]:
    if not _authorized(x_telegram_bot_api_secret_token, TELEGRAM_WEBHOOK_SECRET):
        raise HTTPException(status_code=401)
    # Always 200 once authenticated: a non-2xx makes Telegram resend the update.
    # handle_update reports its own failures to the chat and never raises.
    handlers.handle_update(update)
    return {"ok": True}
