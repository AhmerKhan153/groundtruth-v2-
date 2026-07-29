"""LLM post generation.

Returns the LinkedIn post body as plain text. Persistence and status are handled
by the orchestrator (see src/repository), NOT here, so a draft is only stored once
the caller decides to. The model is obtained from the central factory in src/llm.py.
"""

from src.llm import get_chat_model
from src.config import POST_WRITING_PROMPT_TEMPLATE, REWRITE_PROMPT_SUFFIX


def create_post(title: str, brief: str, is_rewrite: bool = False) -> str:
    """Write a post grounded in `brief`, the factual summary of the source article.

    `brief` comes from the extraction stage (workflow/extraction), not straight
    from the page. It is required: a post written from `title` alone is
    fabrication, so callers must fail rather than pass an empty body.
    """
    if not brief:
        raise ValueError("create_post needs a factual brief; a title alone invites invention")

    prompt = POST_WRITING_PROMPT_TEMPLATE.format(title=title, content=brief)
    if is_rewrite:
        prompt += REWRITE_PROMPT_SUFFIX

    response = get_chat_model().invoke(prompt)
    # LangChain chat models return a message object; fall back to str for safety.
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)