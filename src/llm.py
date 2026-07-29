"""Central LLM factory.

Single seam for the whole app's language model. `LLM_PROVIDER` selects the
backend ("claude" or "ollama"); nothing else in the codebase instantiates a chat
model directly, so switching providers is one env var.

Claude note: on claude-opus-4-8 the sampling parameters (temperature / top_p /
top_k) are rejected with a 400 error, so we deliberately do NOT pass temperature.
Steer tone through the prompt instead (see config.POST_WRITING_PROMPT_TEMPLATE).
"""

from typing import Any, Type

try:
    from src.config import (
        DEFAULT_LLM_MODEL,
        LLM_PROVIDER,
        CLAUDE_MODEL,
        CLAUDE_MAX_TOKENS,
        OLLAMA_NUM_CTX,
        OLLAMA_NUM_GPU,
    )
except ImportError:  # pragma: no cover - fallback for direct script execution
    from config import (
        DEFAULT_LLM_MODEL,
        LLM_PROVIDER,
        CLAUDE_MODEL,
        CLAUDE_MAX_TOKENS,
        OLLAMA_NUM_CTX,
        OLLAMA_NUM_GPU,
    )


def get_chat_model() -> Any:
    """Return a bare chat model for the configured provider."""
    if LLM_PROVIDER == "claude":
        from langchain_anthropic import ChatAnthropic

        # No temperature: claude-opus-4-8 rejects sampling params (400).
        # Reads ANTHROPIC_API_KEY from the environment.
        return ChatAnthropic(model=CLAUDE_MODEL, max_tokens=CLAUDE_MAX_TOKENS)

    # default: local Ollama.
    # num_ctx: smaller context => smaller KV cache => more layers fit on the GPU.
    # num_gpu: force the layer count onto the GPU (Ollama's auto estimate is
    #   conservative and leaves VRAM idle); None lets Ollama decide.
    from langchain_ollama import ChatOllama

    options = {"num_ctx": OLLAMA_NUM_CTX}
    if OLLAMA_NUM_GPU is not None:
        options["num_gpu"] = OLLAMA_NUM_GPU
    return ChatOllama(model=DEFAULT_LLM_MODEL, **options)


def get_structured_llm(schema: Type) -> Any:
    """Return a chat model that emits validated instances of ``schema``."""
    return get_chat_model().with_structured_output(schema)
