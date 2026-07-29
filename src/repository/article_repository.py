import json
from pathlib import Path
from typing import Iterable, Dict, List


class ArticleRepository:
    """Simple file-based repository for article metadata."""

    def __init__(self, storage_dir: str | Path = None):
        self.storage_dir = Path(storage_dir or Path.cwd() / "data")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_articles(self, articles: Iterable[Dict], filename: str = "articles.json") -> Path:
        path = self.storage_dir / filename
        data: List[Dict] = list(articles)
        with path.open("w", encoding="utf-8") as f:
            # default=str so Mongo types (ObjectId, datetime) on approved docs
            # serialize cleanly instead of raising.
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        return path
