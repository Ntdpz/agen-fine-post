from __future__ import annotations

import asyncio
import logging
import re

from demo_scrape.config import AppConfig
from demo_scrape.models import CollectionResult, FacebookPostResult, GoogleResult

_log = logging.getLogger(__name__)


class RelevanceFilter:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def filter_and_sort(
        self,
        question: str,
        results: CollectionResult,
        threshold: float,
    ) -> CollectionResult:
        if results.facebook_posts:
            scored = await asyncio.gather(
                *[self._score_post(question, p) for p in results.facebook_posts]
            )
            for post, score in zip(results.facebook_posts, scored):
                post.relevance_score = score
            results.facebook_posts = sorted(
                [p for p in results.facebook_posts if p.relevance_score >= threshold],
                key=lambda p: p.relevance_score,
                reverse=True,
            )

        if results.google_results:
            scored = await asyncio.gather(
                *[self._score_google(question, r) for r in results.google_results]
            )
            for result, score in zip(results.google_results, scored):
                result.relevance_score = score
            results.google_results = sorted(
                [r for r in results.google_results if r.relevance_score >= threshold],
                key=lambda r: r.relevance_score,
                reverse=True,
            )

        return results

    async def _score_post(self, question: str, post: FacebookPostResult) -> float:
        text = post.post_text[:400]
        return await self._score_text(question, text)

    async def _score_google(self, question: str, result: GoogleResult) -> float:
        text = f"{result.title} {result.snippet}"[:400]
        return await self._score_text(question, text)

    async def _score_text(self, question: str, text: str) -> float:
        try:
            import httpx

            prompt = (
                f"คำถามผู้ใช้: \"{question}\"\n"
                f"เนื้อหา: \"{text}\"\n\n"
                "ให้คะแนนความเกี่ยวข้องระหว่างคำถามกับเนื้อหานี้ โดยให้ตอบเป็นตัวเลข 0-10 เท่านั้น "
                "(0 = ไม่เกี่ยวข้องเลย, 10 = เกี่ยวข้องมากที่สุด) ห้ามอธิบายเพิ่มเติม"
            )
            payload = {
                "model": self._config.ollama_model,
                "prompt": prompt,
                "stream": False,
            }
            _log.debug("LLM relevance request | model=%s  text_snippet=%r",
                       self._config.ollama_model, text[:60])
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self._config.ollama_host}/api/generate", json=payload
                )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "").strip()
                    match = re.search(r"\d+(?:\.\d+)?", raw)
                    if match:
                        score = min(max(float(match.group()) / 10.0, 0.0), 1.0)
                        _log.debug("LLM relevance score=%.2f  raw=%r  snippet=%r",
                                   score, raw, text[:40])
                        return score
        except Exception as exc:
            _log.debug("LLM relevance failed (%s), using keyword fallback | snippet=%r",
                       exc, text[:60])

        score = _keyword_overlap_score(question, text)
        _log.debug("Keyword overlap score=%.2f  snippet=%r", score, text[:40])
        return score


def _keyword_overlap_score(question: str, text: str) -> float:
    q_words = set(re.findall(r"[A-Za-zก-๙]+", question.lower()))
    t_words = set(re.findall(r"[A-Za-zก-๙]+", text.lower()))
    if not q_words:
        return 0.5
    overlap = len(q_words & t_words) / len(q_words)
    return min(overlap * 2.0, 1.0)
