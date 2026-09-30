"""Runtime configuration, read from the environment.

Locally the values come from `.env`; on Cloud Run they are the service's env vars
(load_dotenv is a no-op when there is no file). Nothing here opens a connection:
importing config must stay free, because it runs on every cold start.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


# --- LLM --------------------------------------------------------------------
# DeepSeek, through its OpenAI-compatible API, both locally and on Cloud Run.
# groundtruth/llm.py is the only module that talks to it.
LLM_API_BASE = os.getenv("LLM_API_BASE") or None
# Deliberately no default: a stale baked-in model id surfaces as a confusing 404
# on the first generation instead of a clear configuration error.
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 4096)
# Worst-case pick is fetch (15s) + 2 calls x (timeout x (1 + retries)), which has
# to stay well inside Cloud Run's 300s request timeout.
LLM_TIMEOUT_SECONDS = _int("LLM_TIMEOUT_SECONDS", 45)
LLM_MAX_RETRIES = _int("LLM_MAX_RETRIES", 1)
# Provider-specific request fields as a JSON object, merged into every call.
# DeepSeek thinks by default; this pipeline wants plain completions, so prod sets
#   LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}
# Kept as opaque config so no vendor's parameter names live in the code.
LLM_EXTRA_BODY = json.loads(os.getenv("LLM_EXTRA_BODY") or "{}")

# --- Storage ----------------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI") or "mongodb://localhost:27017/"
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME") or "groundtruth"

# --- Telegram ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# The only chat whose button taps are acted on.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# Telegram echoes this in X-Telegram-Bot-Api-Secret-Token on every webhook call
# (set once with setWebhook), which is how /telegram knows the caller is Telegram.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")

# --- Source job -------------------------------------------------------------
# Cloud Scheduler sends this in X-Job-Secret. Unset = the endpoint refuses all calls.
JOB_SECRET = os.getenv("JOB_SECRET")
STORIES_PER_RUN = _int("STORIES_PER_RUN", 6)
# Minimum gap between delivered pick lists. The scheduled job fires daily; 44h
# makes that every other day while tolerating schedule jitter. Set 0 while
# testing so every run sends.
SOURCE_INTERVAL_HOURS = float(os.getenv("SOURCE_INTERVAL_HOURS") or 44)
