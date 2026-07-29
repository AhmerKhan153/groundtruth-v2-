"""Standalone runner for HackerNews ingestion.

Fetches and previews the current top HN articles for a quick look, but only
*persists* approved docs. Saving is reserved for docs that cleared review — the
in-flight ones live transiently in Mongo (see draft_repository / the Telegram
approval flow) and are never dumped here.
"""

from ingestion.hackernews.hn_ingestor import HackerNewsIngestor
from repository import draft_repository
from repository.article_repository import ArticleRepository


def main() -> None:
    provider = HackerNewsIngestor()
    articles = provider.fetch_top_articles(limit=10)
    print(f"Fetched {len(articles)} top articles (preview only, not saved).")
    for article in articles:
        print(f"  - {article.get('title')}")

    # Only approved docs get written to disk.
    approved = draft_repository.find_approved()
    repo = ArticleRepository(storage_dir="./data")
    path = repo.save_articles(approved)
    print(f"Saved {len(approved)} approved articles to {path}")


if __name__ == "__main__":
    main()
