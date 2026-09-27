import pytest

from groundtruth import pipeline, prompts
from groundtruth.llm import LLMError

BRIEF = "- The fine-tune cost $500 in compute and beat the baseline on catalog review.\n" * 3


@pytest.fixture
def calls(monkeypatch):
    """Record (messages, temperature) per model call; reply brief, then post."""
    sent = []

    def fake_complete(messages, temperature=None):
        sent.append((messages, temperature))
        return BRIEF if len(sent) == 1 else "A post."

    monkeypatch.setattr(pipeline, "complete", fake_complete)
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: "Title\n\n" + "body " * 2000)
    return sent


def test_draft_from_url(calls):
    brief, draft = pipeline.draft_from_url("Title", "https://example.com")
    assert (brief, draft) == (BRIEF, "A post.")
    (extract, t_extract), (write, t_write) = calls
    assert (t_extract, t_write) == (prompts.EXTRACT_TEMPERATURE, prompts.WRITE_TEMPERATURE)
    # Article text is capped, and the writer sees the brief, never the article.
    assert len(extract[1]["content"]) < pipeline.MAX_CONTENT_CHARS + 200
    assert BRIEF in write[1]["content"]
    assert all("body body" not in m["content"] for m in write)


def test_system_prompts_are_static(calls):
    """Per-request text must never leak into a system prompt: that kills caching."""
    pipeline.draft_from_url("A distinctive title", "https://example.com")
    (extract, _), (write, _) = calls
    assert extract[0] == {"role": "system", "content": prompts.EXTRACT_SYSTEM}
    assert write[0] == {"role": "system", "content": prompts.WRITE_SYSTEM}
    assert "A distinctive title" in extract[1]["content"] and "A distinctive title" in write[1]["content"]


def test_rewrite_extends_first_write_call_verbatim():
    """The rewrite's prefix must equal the original write request, byte for byte,
    so DeepSeek serves it from cache; the previous draft follows as assistant."""
    first = pipeline.write_messages("Title", BRIEF)
    rewrite = pipeline.write_messages("Title", BRIEF, previous_draft="Post v1")
    assert rewrite[: len(first)] == first
    assert rewrite[len(first):] == [
        {"role": "assistant", "content": "Post v1"},
        {"role": "user", "content": prompts.REWRITE_USER},
    ]


def test_rewrite_uses_brief_only(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "complete", lambda m, temperature=None: sent.append(m) or "Post v2")
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: pytest.fail("rewrite must not re-fetch"))
    assert pipeline.write_post("Title", BRIEF, previous_draft="Post v1") == "Post v2"
    assert sent[0][-2]["content"] == "Post v1"


def test_unreadable_article_fails_closed(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: None)
    with pytest.raises(pipeline.DraftError, match="read the article"):
        pipeline.draft_from_url("Title", "https://example.com")
    assert calls == []  # the model is never asked to write from a headline


def test_paywall_page_fails_closed(monkeypatch):
    """Page chrome can pass the fetcher's length floor; the extractor's NO_ARTICLE
    verdict must stop the writer from inventing a post."""
    sent = []
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: "Upgrade to Premium " * 50)
    monkeypatch.setattr(pipeline, "complete", lambda m, temperature=None: sent.append(m) or prompts.NO_ARTICLE)
    with pytest.raises(pipeline.DraftError, match="no readable article"):
        pipeline.draft_from_url("Title", "https://example.com")
    assert len(sent) == 1  # extraction only; the writer never ran


def test_thin_brief_fails_closed(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: "text")
    monkeypatch.setattr(pipeline, "complete", lambda m, temperature=None: "- Too short.")
    with pytest.raises(pipeline.DraftError, match="facts"):
        pipeline.draft_from_url("Title", "https://example.com")


def test_model_failure_becomes_draft_error(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_article", lambda url: "text")

    def boom(messages, temperature=None):
        raise LLMError("APITimeoutError: timed out")

    monkeypatch.setattr(pipeline, "complete", boom)
    with pytest.raises(pipeline.DraftError, match="timed out"):
        pipeline.draft_from_url("Title", "https://example.com")
