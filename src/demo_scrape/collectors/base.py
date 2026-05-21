from __future__ import annotations

from abc import ABC, abstractmethod

from demo_scrape.models import SearchPlan


class Collector(ABC):
    source_name: str

    @abstractmethod
    async def collect(self, plan: SearchPlan):
        raise NotImplementedError