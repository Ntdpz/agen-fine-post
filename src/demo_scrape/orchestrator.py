from __future__ import annotations

import asyncio
from dataclasses import replace

from demo_scrape.collectors import FacebookCollector, GoogleCollector
from demo_scrape.config import AppConfig
from demo_scrape.formatter import ResultFormatter
from demo_scrape.models import CollectionResult, SearchPlan, SourceError
from demo_scrape.planner import build_search_plan
from demo_scrape.relevance import RelevanceFilter


class DemoOrchestrator:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._facebook = FacebookCollector(config)
        self._google = GoogleCollector(config)
        self._formatter = ResultFormatter(config)
        self._relevance = RelevanceFilter(config)

    async def handle_question(
        self,
        question: str,
        *,
        plan: SearchPlan | None = None,
        seen_fb_urls: set[str] | None = None,
        seen_google_urls: set[str] | None = None,
    ) -> tuple[SearchPlan, CollectionResult, str]:
        if plan is None:
            plan = build_search_plan(question, self._config)

        overfetch_plan = _make_overfetch_plan(plan, self._config.collection_overfetch_multiplier)
        results = await self._collect(overfetch_plan)

        results = await self._relevance.filter_and_sort(
            question, results, self._config.relevance_threshold
        )

        # Adaptive round: if relevant results are below minimum, collect one more batch
        needs_more_fb = (
            "facebook" in plan.sources
            and len(results.facebook_posts) < self._config.min_relevant_results
        )
        needs_more_google = (
            "google" in plan.sources
            and len(results.google_results) < self._config.min_relevant_results
        )
        if needs_more_fb or needs_more_google:
            extra = await self._collect(overfetch_plan)
            _merge_results(results, extra)
            results = await self._relevance.filter_and_sort(
                question, results, self._config.relevance_threshold
            )

        # Session deduplication: remove items already shown to this user
        if seen_fb_urls is not None:
            results.facebook_posts = [
                p for p in results.facebook_posts
                if not p.post_url or p.post_url not in seen_fb_urls
            ]
        if seen_google_urls is not None:
            results.google_results = [
                r for r in results.google_results if r.url not in seen_google_urls
            ]

        # Trim to requested count
        results.facebook_posts = results.facebook_posts[: plan.max_facebook_posts]
        results.google_results = results.google_results[: plan.max_google_results]

        rendered = await self._formatter.format(plan, results)
        return plan, results, rendered

    async def _collect(self, plan: SearchPlan) -> CollectionResult:
        collected = CollectionResult()
        tasks: list[tuple[str, asyncio.Task]] = []

        if "facebook" in plan.sources:
            tasks.append(("facebook", asyncio.create_task(self._facebook.collect(plan))))
        if "google" in plan.sources:
            tasks.append(("google", asyncio.create_task(self._google.collect(plan))))

        for source_name, task in tasks:
            try:
                payload = await task
            except Exception as exc:
                collected.errors.append(SourceError(source=source_name, message=str(exc)))
                continue

            if source_name == "facebook":
                collected.facebook_posts = payload
            elif source_name == "google":
                collected.google_results = payload

        return collected


def _make_overfetch_plan(plan: SearchPlan, multiplier: int) -> SearchPlan:
    return replace(
        plan,
        max_facebook_posts=plan.max_facebook_posts * multiplier,
        max_google_results=plan.max_google_results * multiplier,
    )


def _merge_results(base: CollectionResult, extra: CollectionResult) -> None:
    seen_fb_urls = {p.post_url for p in base.facebook_posts if p.post_url}
    seen_fb_texts = {p.post_text for p in base.facebook_posts}
    for post in extra.facebook_posts:
        if post.post_url not in seen_fb_urls and post.post_text not in seen_fb_texts:
            base.facebook_posts.append(post)
            if post.post_url:
                seen_fb_urls.add(post.post_url)
            seen_fb_texts.add(post.post_text)

    seen_google_urls = {r.url for r in base.google_results}
    for result in extra.google_results:
        if result.url not in seen_google_urls:
            base.google_results.append(result)
            seen_google_urls.add(result.url)
