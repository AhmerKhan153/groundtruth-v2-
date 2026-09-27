"""The automated stretch of drafting: fetch -> extract -> write.

Plain functions, deliberately. It is a straight line with fail-closed exits,
which is all the old LangGraph graph expressed; dropping the dependency is most
of what keeps the Cloud Run image slim and the cold start fast.

Try it without the bot -- pick from today's Hacker News, or pass a URL:

    python -m groundtruth.pipeline
    python -m groundtruth.pipeline <url> ["optional title"]
"""

import logging
import sys
from typing import List, Optional, Tuple

from groundtruth.fetcher import fetch_article
from groundtruth.llm import LLMError, Message, complete
from groundtruth.prompts import (
    EXTRACT_SYSTEM,
    EXTRACT_TEMPERATURE,
    EXTRACT_USER,
    NO_ARTICLE,
    REWRITE_USER,
    WRITE_SYSTEM,
    WRITE_TEMPERATURE,
    WRITE_USER,
)

# Cap article text fed to the extractor: enough for the substance of a long post
# while keeping each extraction call cheap.
MAX_CONTENT_CHARS = 6000

# A brief thinner than this means extraction found nothing to say. Writing from
# it would be invention, so fail the same way a dead fetch does.
MIN_BRIEF_CHARS = 120


class DraftError(RuntimeError):
    """No grounded draft is possible. The message is safe to show the user."""


def extract_messages(title: str, content: str) -> List[Message]:
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": EXTRACT_USER.format(title=title, content=content)},
    ]


def write_messages(title: str, brief: str, previous_draft: Optional[str] = None) -> List[Message]:
    """The writer's conversation. A rewrite extends the first write call verbatim,
    so its whole prefix is a cache hit and the model sees what to move away from."""
    messages = [
        {"role": "system", "content": WRITE_SYSTEM},
        {"role": "user", "content": WRITE_USER.format(title=title, brief=brief)},
    ]
    if previous_draft:
        messages += [
            {"role": "assistant", "content": previous_draft},
            {"role": "user", "content": REWRITE_USER},
        ]
    return messages


def extract_brief(title: str, url: str) -> str:
    """Fetch the article and distil it to a factual brief, or raise DraftError."""
    article_text = fetch_article(url)
    if not article_text:
        # Deliberately NOT falling back to the title. A post about a headline the
        # model can't read is confident invention, which is worse than no draft.
        raise DraftError("Could not read the article text from the source page.")

    try:
        brief = complete(
            extract_messages(title, article_text[:MAX_CONTENT_CHARS]),
            temperature=EXTRACT_TEMPERATURE,
        )
    except LLMError as exc:
        raise DraftError(f"The model call failed while extracting facts: {exc}") from exc

    # Page chrome can be long enough to pass the fetcher's length floor (a paywall
    # page did), so the extractor is also asked whether there is an article at all.
    if brief.upper().startswith(NO_ARTICLE):
        raise DraftError("The page has no readable article (paywall, login wall or script-rendered page).")
    if len(brief) < MIN_BRIEF_CHARS:
        raise DraftError("Could not pull any facts out of the source page.")
    return brief


def write_post(title: str, brief: str, previous_draft: Optional[str] = None) -> str:
    """Write the post from `brief` alone, or raise DraftError.

    Pass `previous_draft` for a rewrite: the stored brief is reused, so a rewrite is
    one model call with no re-fetch.
    """
    if not brief:
        raise DraftError("No factual brief to write from; a title alone invites invention.")
    try:
        return complete(write_messages(title, brief, previous_draft), temperature=WRITE_TEMPERATURE)
    except LLMError as exc:
        raise DraftError(f"The model call failed while writing the post: {exc}") from exc


def draft_from_url(title: str, url: str) -> Tuple[str, str]:
    """Return (brief, draft) for a story. The brief is kept for cheap rewrites."""
    brief = extract_brief(title, url)
    return brief, write_post(title, brief)


# --- manual testing ----------------------------------------------------------

def _pick_story() -> Optional[Tuple[str, str]]:
    from groundtruth.sources.hackernews import fetch_candidates

    print("Fetching today's Hacker News front page...")
    stories = fetch_candidates()[:10]
    if not stories:
        print("No stories found.")
        return None
    for i, s in enumerate(stories, 1):
        print(f"{i:>2}. {s['title']}  ({s['score']}▲ {s['comments']}💬)\n    {s['url']}")
    choice = input("\nPick a number (Enter to quit): ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(stories):
        return None
    story = stories[int(choice) - 1]
    return story["url"], story["title"]


def _main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="  · %(message)s")
    logging.getLogger("groundtruth").setLevel(logging.INFO)  # llm call stats only
    if argv:
        url, title = argv[0], (argv[1] if len(argv) > 1 else "")
    else:
        picked = _pick_story()
        if not picked:
            return 0
        url, title = picked

    print(f"\nDrafting: {title or url}\n")
    try:
        brief, draft = draft_from_url(title, url)
    except DraftError as exc:
        print(f"No draft: {exc}")
        return 1
    print(f"\n--- brief ({len(brief)} chars) ---\n{brief}\n")

    while True:
        paragraphs = len([p for p in draft.split("\n\n") if p.strip()])
        print(f"\n--- draft ({len(draft.split())} words, {paragraphs} paragraphs) ---\n{draft}\n")
        if input("[r] rewrite from a different angle, Enter to finish: ").strip().lower() != "r":
            return 0
        try:
            draft = write_post(title, brief, previous_draft=draft)
        except DraftError as exc:
            print(f"Rewrite failed: {exc}")
            return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
