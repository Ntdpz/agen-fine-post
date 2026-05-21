from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from demo_scrape.collectors import FacebookCollector, GoogleCollector
from demo_scrape.config import AppConfig
from demo_scrape.formatter import ResultFormatter
from demo_scrape.models import CollectionResult, SearchPlan, SourceError
from demo_scrape.planner import build_search_plan
from demo_scrape.relevance import RelevanceFilter

_log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


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
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[SearchPlan, CollectionResult, str]:
        async def _notify(msg: str) -> None:
            _log.info(msg)
            if progress_callback:
                try:
                    await progress_callback(msg)
                except Exception:
                    pass

        if plan is None:
            plan = build_search_plan(question, self._config)

        sources_label = " + ".join(s.capitalize() for s in plan.sources)
        _log.info("handle_question start | question=%r  keyword=%r  sources=%s",
                  question[:80], plan.keyword, plan.sources)

        overfetch_plan = _make_overfetch_plan(plan, self._config.collection_overfetch_multiplier)

        await _notify(f"📡 กำลังดึงข้อมูลจาก {sources_label}...")
        _log.debug("Overfetch plan: max_fb=%d  max_google=%d",
                   overfetch_plan.max_facebook_posts, overfetch_plan.max_google_results)
        results = await self._collect(overfetch_plan)
        _log.info("Collection done | fb_posts=%d  google_results=%d  errors=%d",
                  len(results.facebook_posts), len(results.google_results), len(results.errors))
        if results.errors:
            for err in results.errors:
                _log.warning("Collection error [%s]: %s", err.source, err.message)

        total_items = len(results.facebook_posts) + len(results.google_results)
        await _notify(f"🤖 AI กำลังประเมินความเกี่ยวข้อง ({total_items} รายการ)...")
        results = await self._relevance.filter_and_sort(
            question, results, self._config.relevance_threshold
        )
        _log.info("Relevance filter done | fb_kept=%d  google_kept=%d",
                  len(results.facebook_posts), len(results.google_results))

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
            _log.info("Adaptive round triggered | needs_more_fb=%s  needs_more_google=%s",
                      needs_more_fb, needs_more_google)
            await _notify(f"🔄 ผลลัพธ์น้อยเกินไป กำลังดึงข้อมูลเพิ่มเติม...")
            extra = await self._collect(overfetch_plan)
            _merge_results(results, extra)
            results = await self._relevance.filter_and_sort(
                question, results, self._config.relevance_threshold
            )
            _log.info("Adaptive round done | fb_kept=%d  google_kept=%d",
                      len(results.facebook_posts), len(results.google_results))

        # Session deduplication: remove items already shown to this user
        if seen_fb_urls is not None:
            before = len(results.facebook_posts)
            results.facebook_posts = [
                p for p in results.facebook_posts
                if not p.post_url or p.post_url not in seen_fb_urls
            ]
            _log.debug("Session dedup FB: %d → %d", before, len(results.facebook_posts))
        if seen_google_urls is not None:
            before = len(results.google_results)
            results.google_results = [
                r for r in results.google_results if r.url not in seen_google_urls
            ]
            _log.debug("Session dedup Google: %d → %d", before, len(results.google_results))

        # Trim to requested count
        results.facebook_posts = results.facebook_posts[: plan.max_facebook_posts]
        results.google_results = results.google_results[: plan.max_google_results]

        await _notify("✍️ AI กำลังสรุปผลลัพธ์...")
        rendered = await self._formatter.format(plan, results)
        _log.info("Formatting done | rendered_len=%d chars", len(rendered))

        _log.info("handle_question complete | fb=%d  google=%d",
                  len(results.facebook_posts), len(results.google_results))
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
