from typing import Dict, Type

from ingestion.article_provider import ArticleProvider


class ProviderRegistry:

    def __init__(self):
        self._providers: Dict[str, Type[ArticleProvider]] = {}

    def register(self, name: str, provider: Type[ArticleProvider]) -> None:
        self._providers[name.lower()] = provider

    def get(self, name: str) -> Type[ArticleProvider]:
        return self._providers[name.lower()]

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())


registry = ProviderRegistry()
