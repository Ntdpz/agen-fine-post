from __future__ import annotations

import re

from demo_scrape.config import AppConfig
from demo_scrape.models import SearchPlan


_SOURCE_HINTS = {
    "facebook": ("facebook", "fb", "โพสต์", "คอมเมนต์", "comment"),
    "google": ("google", "ข่าว", "เว็บ", "ผลค้นหา", "search"),
}

_NOISE_PATTERNS = [
    r"ค้นหาโพสต์",
    r"ค้น(?:หา)?",
    r"เรื่อง",
    r"ของ",
    r"หาข้อมูล(?:ใน|จาก)?",
    r"ช่วย(?:หา|สรุป)?",
    r"ว่า",
    r"แล้วสรุป(?:ข่าว)?ล่าสุดให้หน่อย",
    r"สรุป(?:ข่าว)?ล่าสุดให้หน่อย",
    r"ตอนนี้",
    r"ยังไงบ้าง",
    r"คอมเมนต์",
    r"มีข้อมูลอะไรบ้าง",
    r"บน",
    r"ใน",
    r"จาก",
    r"และ",
    r"facebook",
    r"google",
]


def build_search_plan(
    question: str,
    config: AppConfig,
    *,
    sources_override: tuple[str, ...] | None = None,
    max_posts_override: int | None = None,
    max_comments_override: int | None = None,
) -> SearchPlan:
    cleaned_question = question.strip()
    lowered = cleaned_question.lower()

    if sources_override is not None:
        sources = list(sources_override)
    else:
        sources = [source for source, hints in _SOURCE_HINTS.items() if any(hint in lowered for hint in hints)]
        if not sources:
            sources = ["facebook", "google"]

    keyword = _extract_keyword(cleaned_question)

    return SearchPlan(
        raw_question=cleaned_question,
        keyword=keyword,
        sources=tuple(sources),
        max_facebook_posts=max_posts_override if max_posts_override is not None else config.max_facebook_posts,
        max_google_results=max_posts_override if max_posts_override is not None else config.max_google_results,
        max_comments_per_post=max_comments_override if max_comments_override is not None else config.default_max_comments_per_post,
    )


def _extract_keyword(question: str) -> str:
    quoted_match = re.search(r'["“](.+?)["”]', question)
    if quoted_match:
        return quoted_match.group(1).strip()

    reduced = question
    for pattern in _NOISE_PATTERNS:
        reduced = re.sub(pattern, " ", reduced, flags=re.IGNORECASE)

    reduced = re.sub(r"[^0-9A-Za-zก-๙\s-]", " ", reduced)
    reduced = re.sub(r"\s+", " ", reduced).strip()
    if not reduced:
        return question.strip()

    tokens = reduced.split()
    if len(tokens) == 1:
        return tokens[0]

    connectors = {"กับ", "and", "or", "และ", "แล้ว"}
    selected: list[str] = []
    for token in tokens:
        if token.lower() in connectors:
            break
        selected.append(token)
        if len(selected) == 4:
            break

    return " ".join(selected).strip() or reduced