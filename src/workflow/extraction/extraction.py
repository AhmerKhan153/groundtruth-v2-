"""Stage 1 of drafting: reduce article text to a factual brief.

The brief is what grounds the post. A small local model cannot both digest a long
article and obey a long list of voice rules in one pass -- it drops the article.
Pulling the facts out first means the writing prompt only has to handle a few
hundred characters of already-distilled fact.
"""

from src.config import FACT_EXTRACTION_PROMPT_TEMPLATE
from src.llm import get_chat_model


def extract_facts(title: str, content: str) -> str:
    """Return a bulleted factual brief drawn from `content`.

    `content` is required for the same reason it is in create_post: a brief
    invented from a headline is worse than no brief, because everything
    downstream treats these bullets as verified fact.
    """
    if not content:
        raise ValueError("extract_facts needs article text; a title alone invites invention")

    prompt = FACT_EXTRACTION_PROMPT_TEMPLATE.format(title=title, content=content)
    response = get_chat_model().invoke(prompt)
    # LangChain chat models return a message object; fall back to str for safety.
    text = getattr(response, "content", response)
    return (text if isinstance(text, str) else str(text)).strip()


class ExtractionWorkflow:
    def extract(self, title: str, content: str) -> str:
        """Return the factual brief for `content` as plain text."""
        return extract_facts(title, content)
