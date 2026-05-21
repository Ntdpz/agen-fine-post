from __future__ import annotations

import logging
import re

from demo_scrape.config import AppConfig
from demo_scrape.models import SearchPlan

_log = logging.getLogger(__name__)


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


async def build_search_plan(
    question: str,
    config: AppConfig,
    *,
    sources_override: tuple[str, ...] | None = None,
    max_posts_override: int | None = None,
    max_comments_override: int | None = None,
) -> SearchPlan:
    cleaned_question = question.strip()
    lowered = cleaned_question.lower()
    _log.info("Building search plan | question=%r", cleaned_question[:100])

    if sources_override is not None:
        sources = list(sources_override)
        _log.debug("Sources overridden: %s", sources)
    else:
        sources = [source for source, hints in _SOURCE_HINTS.items() if any(hint in lowered for hint in hints)]
        if not sources:
            sources = ["facebook", "google"]

    llm_keyword = await _extract_keyword_with_llm(cleaned_question, config)
    if llm_keyword:
        keyword = llm_keyword
    else:
        _log.debug("LLM keyword unavailable, falling back to rule-based extractor")
        keyword = _extract_keyword(cleaned_question)

    _log.info("Plan ready | keyword=%r  sources=%s  max_posts=%s  max_google=%s",
              keyword, sources,
              max_posts_override or config.max_facebook_posts,
              max_posts_override or config.max_google_results)

    return SearchPlan(
        raw_question=cleaned_question,
        keyword=keyword,
        sources=tuple(sources),
        max_facebook_posts=max_posts_override if max_posts_override is not None else config.max_facebook_posts,
        max_google_results=max_posts_override if max_posts_override is not None else config.max_google_results,
        max_comments_per_post=max_comments_override if max_comments_override is not None else config.default_max_comments_per_post,
    )


async def _extract_keyword_with_llm(question: str, config: AppConfig) -> str | None:
    """Call Ollama to extract a concise search keyword. Returns None on any failure."""
    try:
        import httpx
    except ImportError:
        return None
    prompt = (
        f"จากคำถามต่อไปนี้ ให้ตอบเฉพาะ search keyword ภาษาไทยหรืออังกฤษ 1-5 คำ เป็น phrase เดียว "
        f"ห้ามมีจุลภาค ห้ามแบ่งเป็น list ห้ามอธิบายเพิ่มเติม:\n"
        f"{question}"
    )
    payload = {
        "model": config.ollama_model,
        "stream": False,
        "prompt": prompt,
        "options": {"num_predict": 50},
        "think": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{config.ollama_host}/api/generate", json=payload
            )
            response.raise_for_status()
        result: str = response.json().get("response", "").strip()
        # Strip thinking tags (<think>...</think>) from models like qwen3
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
        # Keep only the first line if multi-line
        result = result.splitlines()[0].strip() if result else ""
        # If model returned a comma-separated list, take only the first item
        result = result.split(",")[0].strip()
        if result and _is_safe_keyword(result):
            _log.info("LLM keyword extracted: %r", result)
            return result
        if result:
            _log.warning("LLM keyword rejected (unexpected Unicode): %r", result)
    except Exception as exc:
        _log.warning("LLM keyword extraction failed: %s", exc)
    return None


# Allow only: Thai (U+0E00-U+0E7F), ASCII alphanumeric, space, hyphen, slash, plus, dot
_SAFE_KEYWORD_RE = re.compile(r"^[\u0E00-\u0E7F0-9A-Za-z \-/+.]+$")


def _is_safe_keyword(text: str) -> bool:
    """Return True only if keyword contains no unexpected Unicode scripts (e.g. Cyrillic)."""
    return bool(_SAFE_KEYWORD_RE.match(text))


def _extract_keyword(question: str) -> str:
    quoted_match = re.search(r'["“](.+?)["”]', question)
    if quoted_match:
        return quoted_match.group(1).strip()

    reduced = question
    for pattern in _NOISE_PATTERNS:
        reduced = re.sub(pattern, " ", reduced, flags=re.IGNORECASE)

    reduced = re.sub(r"[^0-9A-Za-zก-๙+.\s-]", " ", reduced)
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
        if len(selected) == 15:
            break

    return " ".join(selected).strip() or reduced