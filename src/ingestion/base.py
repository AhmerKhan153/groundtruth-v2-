from abc import ABC, abstractmethod
from typing import List, Dict


class Ingestor(ABC):

    @abstractmethod
    def fetch_top_articles(self, limit: int = 20) -> List[Dict[str, str]]:
        raise NotImplementedError()
