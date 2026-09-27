"""The app's single LLM seam.

Nothing else in the codebase talks to a model. The endpoint speaks the OpenAI chat
protocol (DeepSeek in production), configured entirely by LLM_* env vars, so no
vendor name or parameter lives in the code; provider-specific switches such as
DeepSeek's thinking toggle go through LLM_EXTRA_BODY.

Every call logs one line (latency, tokens, cache hits), which is how prompt-cache
behaviour is checked locally and in Cloud Run's logs.
"""

import logging
import time
from typing import Dict, List, Optional

from openai import OpenAI, OpenAIError

from groundtruth.config import (
    LLM_API_BASE,
    LLM_API_KEY,
    LLM_EXTRA_BODY,
    LLM_MAX_RETRIES,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

Message = Dict[str, str]


class LLMError(RuntimeError):
    """The model call failed or returned nothing usable."""


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Build the client on first use, so importing this module stays free."""
    global _client
    if _client is None:
        if not LLM_MODEL:
            raise LLMError(
                "LLM_MODEL is not set. Set it to the model id your endpoint expects "
                "(and LLM_API_BASE to the endpoint) in .env or the service env."
            )
        _client = OpenAI(
            # Omitted when blank so the SDK falls back to its own env variable.
            **({"api_key": LLM_API_KEY} if LLM_API_KEY else {}),
            base_url=LLM_API_BASE,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )
    return _client


def complete(messages: List[Message], temperature: Optional[float] = None) -> str:
    """Send a chat and return the reply text, stripped.

    `messages` keeps the static system prompt first and per-request content last;
    see prompts.py for why that ordering is what makes cache hits possible.
    """
    started = time.monotonic()
    try:
        response = _get_client().chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
            **({"temperature": temperature} if temperature is not None else {}),
            extra_body=LLM_EXTRA_BODY or None,
        )
    except OpenAIError as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc

    usage = response.usage
    if usage is not None:
        log.info(
            "llm %s %.1fs in=%s (cache hit %s / miss %s) out=%s",
            LLM_MODEL,
            time.monotonic() - started,
            usage.prompt_tokens,
            getattr(usage, "prompt_cache_hit_tokens", "?"),
            getattr(usage, "prompt_cache_miss_tokens", "?"),
            usage.completion_tokens,
        )

    text = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not text:
        raise LLMError("The model returned an empty reply.")
    return text
