from __future__ import annotations

from html import unescape
import re
from xml.etree import ElementTree
from urllib.parse import parse_qs, quote_plus, urlparse

from demo_scrape.collectors.base import Collector
from demo_scrape.config import AppConfig
from demo_scrape.models import GoogleResult, SearchPlan


class GoogleCollector(Collector):
    source_name = "google"

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def collect(self, plan: SearchPlan) -> list[GoogleResult]:
        if self._config.use_stub_results:
            return _stub_results(plan.keyword, plan.max_google_results)

        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise RuntimeError("Google collector dependencies are not installed") from exc

        url = f"https://www.google.com/search?hl=th&num={plan.max_google_results}&q={quote_plus(plan.keyword)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        }

        async with httpx.AsyncClient(timeout=self._config.request_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results: list[GoogleResult] = []
        seen_urls: set[str] = set()

        for card in soup.select("div.g, div.MjjYud"):
            anchor = card.select_one("div.yuRUbf a, a[href^='/url?q=']")
            title_node = card.select_one("h3")
            snippet_node = card.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf='1']")

            if not anchor or not title_node:
                continue

            url_value = _normalize_google_href(anchor.get("href", ""))
            if not url_value or url_value in seen_urls:
                continue

            seen_urls.add(url_value)
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            results.append(
                GoogleResult(
                    title=unescape(title_node.get_text(" ", strip=True)),
                    snippet=unescape(snippet),
                    source=urlparse(url_value).netloc,
                    url=url_value,
                )
            )
            if len(results) >= plan.max_google_results:
                break

        if results:
            return results

        for anchor in soup.select("a[href^='/url?q=']"):
            href = anchor.get("href", "")
            url_value = _normalize_google_href(href)
            title = anchor.get_text(" ", strip=True)
            if not url_value or not title or url_value in seen_urls:
                continue
            seen_urls.add(url_value)
            results.append(
                GoogleResult(
                    title=title,
                    snippet="",
                    source=urlparse(url_value).netloc,
                    url=url_value,
                )
            )
            if len(results) >= plan.max_google_results:
                break

        if results:
            return results

        return await self._collect_google_news_rss(plan)

    async def _collect_google_news_rss(self, plan: SearchPlan) -> list[GoogleResult]:
        import httpx

        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(plan.keyword)}&hl=th&gl=TH&ceid=TH:th"
        )

        async with httpx.AsyncClient(timeout=self._config.request_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(rss_url)
            response.raise_for_status()

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise RuntimeError("No Google results were parsed from the response") from exc

        results: list[GoogleResult] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source = (item.findtext("source") or urlparse(link).netloc).strip()
            description = _description_to_text(item.findtext("description") or "")
            if not title or not link:
                continue
            results.append(
                GoogleResult(
                    title=title,
                    snippet=description,
                    source=source,
                    url=link,
                )
            )
            if len(results) >= plan.max_google_results:
                break

        if not results:
            raise RuntimeError("No Google results were parsed from the response")

        return results


def _normalize_google_href(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("/url?"):
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        target = query.get("q", [None])[0]
        if not target:
            return None
        return re.sub(r"#.*$", "", target)
    if href.startswith("http://") or href.startswith("https://"):
        return re.sub(r"#.*$", "", href)
    return None


def _stub_results(keyword: str, limit: int) -> list[GoogleResult]:
    results = [
        GoogleResult(
            title=f"อัปเดตล่าสุดเกี่ยวกับ {keyword}",
            snippet=f"สรุปข้อมูลล่าสุดของ {keyword} จากผลค้นหาตัวอย่างสำหรับเดโม",
            source="news.example.com",
            url=f"https://news.example.com/search/{quote_plus(keyword)}",
        ),
        GoogleResult(
            title=f"ประเด็นสำคัญของ {keyword}",
            snippet=f"รวบรวมข่าวและข้อมูลที่เกี่ยวข้องกับ {keyword}",
            source="search.example.org",
            url=f"https://search.example.org/topics/{quote_plus(keyword)}",
        ),
        GoogleResult(
            title=f"ไทม์ไลน์ {keyword}",
            snippet=f"บทสรุปภาพรวมที่อ่านเร็วสำหรับ {keyword}",
            source="timeline.example.net",
            url=f"https://timeline.example.net/{quote_plus(keyword)}",
        ),
    ]
    return results[:limit]


def _description_to_text(description: str) -> str:
    if not description:
        return ""
    # RSS descriptions are HTML fragments; remove tags while keeping readable text.
    text = re.sub(r"<[^>]+>", " ", description)
    return re.sub(r"\s+", " ", unescape(text)).strip()