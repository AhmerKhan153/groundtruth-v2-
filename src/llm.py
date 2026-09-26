"""Central LLM factory -- the app's single provider seam.

Nothing else in the codebase constructs a chat model, so switching backends is one
env var (LLM_PROVIDER), and adding a backend is one function plus its SDK. That is
the whole point of this module: no vendor name appears anywhere else in the
pipeline, so the drafting logic cannot quietly grow a dependency on one provider's
behaviour.

Adapters are imported lazily inside each builder, so you only need the SDK for the
provider you actually use.

No sampling parameters (temperature / top_p / top_k) are passed to the hosted
providers. Some current models reject them outright with a 400, and this
pipeline's output quality comes from the two-stage prompt structure rather than
from sampling. Tone is steered through the prompt -- see
config.POST_WRITING_PROMPT_TEMPLATE.
"""

from typing import Any, Callable, Dict, Type

try:
    from src.config import (
        LLM_API_BASE,
        LLM_API_KEY,
        LLM_MAX_TOKENS,
        LLM_MODEL,
        LLM_PROVIDER,
        OLLAMA_MODEL,
        OLLAMA_NUM_CTX,
        OLLAMA_NUM_GPU,
    )
except ImportError:  # pragma: no cover - fallback for direct script execution
    from config import (
        LLM_API_BASE,
        LLM_API_KEY,
        LLM_MAX_TOKENS,
        LLM_MODEL,
        LLM_PROVIDER,
        OLLAMA_MODEL,
        OLLAMA_NUM_CTX,
        OLLAMA_NUM_GPU,
    )


def _require(module: str, attr: str, package: str) -> Any:
    """Import an adapter class, turning a missing optional SDK into advice.

    Adapters are optional dependencies: installing every provider's SDK to use one
    of them is waste. The bare ImportError names the module, not the thing to
    install, so translate it.
    """
    try:
        return getattr(__import__(module, fromlist=[attr]), attr)
    except ImportError as exc:  # pragma: no cover - depends on local installs
        raise RuntimeError(
            f"LLM_PROVIDER={LLM_PROVIDER!r} needs the {package!r} package: "
            f"pip install {package}"
        ) from exc


def _hosted_kwargs() -> Dict[str, Any]:
    """Arguments common to every hosted adapter.

    Fails loudly on a missing model id: the alternative is an opaque 404 from the
    provider on the first generation, hours after the misconfiguration.
    """
    if not LLM_MODEL:
        raise RuntimeError(
            f"LLM_PROVIDER={LLM_PROVIDER!r} needs a model id. Set LLM_MODEL in "
            f".env to whatever your provider calls the model you want, or set "
            f"LLM_PROVIDER=ollama to run locally."
        )
    kwargs: Dict[str, Any] = {"model": LLM_MODEL, "max_tokens": LLM_MAX_TOKENS}
    # Omitted rather than passed as None, so the SDK falls back to reading its own
    # conventional key variable from the environment.
    if LLM_API_KEY:
        kwargs["api_key"] = LLM_API_KEY
    return kwargs


def _build_local() -> Any:
    """A model served by a local Ollama daemon.

    num_ctx: a smaller context means a smaller KV cache, which frees VRAM for more
        model layers. num_gpu: force the layer count onto the GPU, because
        Ollama's auto estimate is conservative and leaves VRAM idle. None lets
        Ollama decide. See LOCAL_LLM_GPU.md.
    """
    ChatOllama = _require("langchain_ollama", "ChatOllama", "langchain-ollama")

    options: Dict[str, Any] = {"num_ctx": OLLAMA_NUM_CTX}
    if OLLAMA_NUM_GPU is not None:
        options["num_gpu"] = OLLAMA_NUM_GPU
    return ChatOllama(model=OLLAMA_MODEL, **options)


def _build_openai_compatible() -> Any:
    """Any endpoint speaking the OpenAI chat protocol.

    LLM_API_BASE covers self-hosted servers (vLLM, llama.cpp, LM Studio) and
    third-party gateways; left blank it talks to the SDK's own default host.
    """
    kwargs = _hosted_kwargs()          # validate config before importing the SDK
    if LLM_API_BASE:
        kwargs["base_url"] = LLM_API_BASE
    ChatOpenAI = _require("langchain_openai", "ChatOpenAI", "langchain-openai")
    return ChatOpenAI(**kwargs)


# To add a backend: write a builder that returns any object exposing .invoke() and
# .with_structured_output() -- every LangChain chat model does -- and register it
# here. Validate configuration before importing its SDK, and pull the SDK in through
# _require so a missing install reports what to install. Nothing outside this module
# needs to change; that is what keeps the pipeline provider-agnostic.
_PROVIDERS: Dict[str, Callable[[], Any]] = {
    "ollama": _build_local,
    "openai": _build_openai_compatible,
}


def get_chat_model() -> Any:
    """Return a bare chat model for the configured provider."""
    try:
        build = _PROVIDERS[LLM_PROVIDER]
    except KeyError:
        supported = ", ".join(sorted(_PROVIDERS))
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {LLM_PROVIDER!r}. Supported: {supported}."
        ) from None
    return build()


def get_structured_llm(schema: Type) -> Any:
    """Return a chat model that emits validated instances of ``schema``."""
    return get_chat_model().with_structured_output(schema)
