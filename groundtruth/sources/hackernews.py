"""Hacker News sourcing: fetch the front page, filter, rank.

Item lookups run in a thread pool over one pooled session: 100 sequential
requests took ~15s, and without connection reuse every lookup pays its own TLS
handshake (measured 56s -> 12s on a slow link). That is billed Cloud Run time
spent waiting on the network.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import requests

TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{story_id}.json"

# How many top ids to inspect before ranking. Bigger = better ranking, slower.
SCAN_LIMIT = 100
_WORKERS = 16
_TIMEOUT = 10

MIN_SCORE = 50
# Runs are ~48h apart; a 72h window covers the gap without resurfacing old news.
# Dedup against stories already shown happens in the store, not here.
MAX_AGE_SECONDS = 72 * 3600

_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=_WORKERS))


def _fetch_item(story_id: int) -> Dict:
    try:
        return _session.get(ITEM_URL.format(story_id=story_id), timeout=_TIMEOUT).json() or {}
    except (requests.RequestException, ValueError):
        return {}


def rank_score(story: Dict) -> float:
    """Blend upvotes and discussion volume so 'talked-about' stories rank well."""
    return story.get("score", 0) + 2 * story.get("comments", 0)


def to_candidate(item: Dict, now: float) -> Dict:
    """Map an HN item to a candidate story, or {} if it doesn't qualify."""
    # Ask HN / self-posts have no external url to fetch, so no grounded draft.
    if not item.get("url") or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
        return {}
    if item.get("score", 0) < MIN_SCORE:
        return {}
    if now - item.get("time", 0) > MAX_AGE_SECONDS:
        return {}
    return {
        "hn_id": item["id"],
        "title": item.get("title") or "Untitled",
        "url": item["url"],
        "score": item.get("score", 0),
        "comments": item.get("descendants", 0),
    }


def fetch_candidates() -> List[Dict]:
    """Return every qualifying front-page story, best first."""
    try:
        ids = _session.get(TOP_STORIES_URL, timeout=_TIMEOUT).json() or []
    except (requests.RequestException, ValueError):
        return []

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        items = list(pool.map(_fetch_item, ids[:SCAN_LIMIT]))

    now = time.time()
    candidates = [c for c in (to_candidate(item, now) for item in items) if c]
    candidates.sort(key=rank_score, reverse=True)
    return candidates


if __name__ == "__main__":
    for story in fetch_candidates()[:10]:
        print(f"{story['score']:>5}▲ {story['comments']:>4}💬  {story['title']}")
