"""LangGraph subgraph for the automated content stretch: fetch -> extract -> write.

It contains NO human-in-the-loop
node — approval and publishing are driven by Telegram callbacks (see
src/integration). Given a picked story it fetches + cleans the article, distils
it to a factual brief, and hands that brief to the writer.

The extract step exists because one combined prompt does not survive a small
local model: asked to digest a long article *and* obey a long list of voice
rules, gemma2:9b dropped the article and wrote generic filler. Two short prompts
beat one long one.
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from processing.fetcher.fetcher import fetch_article
from processing.cleaner.cleaner import ArticleCleaner
from src.workflow.extraction.extraction import ExtractionWorkflow
from src.workflow.writing.writing import WritingWorkflow

# Cap article text fed to the extractor. Raised from 4000 once the writing prompt
# shrank: extraction reads this, and its prompt is small, so more of the article
# now fits inside OLLAMA_NUM_CTX than before.
_MAX_CONTENT_CHARS = 6000

# A brief thinner than this means extraction found nothing to say -- the page was
# navigation, a paywall teaser, or boilerplate that cleared the fetcher's floor.
# Writing from it would be invention, so fail the same way a dead fetch does.
_MIN_BRIEF_CHARS = 120


class ContentState(TypedDict, total=False):
    title: str
    url: str
    is_rewrite: bool
    content: str
    brief: str
    draft: str
    error: str


def _fetch_node(state: ContentState) -> dict:
    article_text = fetch_article(state.get("url"))
    if not article_text:
        # Deliberately NOT falling back to the title. Asking the model for
        # 150-250 words about a headline it can't read produces confident
        # invention -- fake numbers, fake quotes, fake detail -- which is worse
        # than no draft at all. Fail loudly instead.
        return {"error": "Could not read the article text from the source page."}

    cleaned = ArticleCleaner().clean(
        {
            "title": state.get("title"),
            "url": state.get("url"),
            "articlehtml": article_text,
        }
    )
    return {"content": (cleaned.get("content") or "")[:_MAX_CONTENT_CHARS]}


def _extract_node(state: ContentState) -> dict:
    brief = ExtractionWorkflow().extract(
        state.get("title", ""), state.get("content", "")
    )
    if len(brief) < _MIN_BRIEF_CHARS:
        return {"error": "Could not pull any facts out of the source page."}
    return {"brief": brief}


def _write_node(state: ContentState) -> dict:
    draft = WritingWorkflow().write(
        state.get("title", ""),
        state.get("brief", ""),
        is_rewrite=state.get("is_rewrite", False),
    )
    return {"draft": draft}


def _after_fetch(state: ContentState) -> str:
    return END if state.get("error") else "extract"


def _after_extract(state: ContentState) -> str:
    return END if state.get("error") else "write"


def _build_graph():
    builder = StateGraph(ContentState)
    builder.add_node("fetch", _fetch_node)
    builder.add_node("extract", _extract_node)
    builder.add_node("write", _write_node)
    builder.set_entry_point("fetch")
    builder.add_conditional_edges(
        "fetch", _after_fetch, {"extract": "extract", END: END}
    )
    builder.add_conditional_edges(
        "extract", _after_extract, {"write": "write", END: END}
    )
    builder.add_edge("write", END)
    return builder.compile()


_graph = _build_graph()


class ArticleUnavailable(RuntimeError):
    """The source article couldn't be read, so no grounded draft is possible."""


def generate_draft(title: str, url: str, is_rewrite: bool = False) -> str:
    result = _graph.invoke(
        {"title": title, "url": url, "is_rewrite": is_rewrite}
    )
    if result.get("error"):
        raise ArticleUnavailable(result["error"])
    return result.get("draft", "")
