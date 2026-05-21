from __future__ import annotations

import html
import json
from urllib.parse import urlparse

from demo_scrape.config import AppConfig
from demo_scrape.models import CollectionResult, SearchPlan


async def _shorten_url(url: str) -> str:
    """Shorten a URL via TinyURL. Falls back to the original URL on any error."""
    if not url:
        return url
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://tinyurl.com/api-create.php", params={"url": url}
            )
            if resp.status_code == 200 and resp.text.strip().startswith("http"):
                return resp.text.strip()
    except Exception:
        pass
    return url


class ResultFormatter:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def format(self, plan: SearchPlan, results: CollectionResult) -> str:
        llm_output = await self._try_ollama(plan, results)
        if llm_output and self._is_valid(llm_output):
            return llm_output.strip()
        return await self._fallback_render(plan, results)

    async def _try_ollama(self, plan: SearchPlan, results: CollectionResult) -> str | None:
        try:
            import httpx
        except ImportError:
            return None

        payload = {
            "model": self._config.ollama_model,
            "stream": False,
            "prompt": self._build_prompt(plan, results),
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.request_timeout_seconds) as client:
                response = await client.post(f"{self._config.ollama_host}/api/generate", json=payload)
                response.raise_for_status()
        except Exception:
            return None

        data = response.json()
        return data.get("response")

    def _build_prompt(self, plan: SearchPlan, results: CollectionResult) -> str:
        return (
            "สรุปข้อมูลต่อไปนี้เป็นข้อความสำหรับ Telegram\n\n"
            "เงื่อนไข:\n"
            "- ตอบเป็นภาษาไทย\n"
            "- แบ่งส่วน Facebook และ Google ชัดเจน\n"
            "- ถ้าไม่มี comment_url ให้เขียนว่า \"ไม่มีลิงก์คอมเมนต์\"\n"
            "- อย่าสร้างข้อมูลที่ไม่มีใน input\n\n"
            f"คำถามผู้ใช้: {plan.raw_question}\n"
            f"Keyword: {plan.keyword}\n\n"
            f"Input JSON:\n{json.dumps(results.to_dict(), ensure_ascii=False, indent=2)}"
        )

    def _is_valid(self, text: str) -> bool:
        if "Facebook" not in text or "Google" not in text:
            return False
        return len(text.strip()) <= self._config.telegram_message_limit

    async def _fallback_render(self, plan: SearchPlan, results: CollectionResult) -> str:
        lines: list[str] = []
        kw = html.escape(plan.keyword)
        lines.append(f"🔍 <b>สรุปข้อมูลเกี่ยวกับ {kw}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.extend(await self._render_facebook(results))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.extend(await self._render_google(results))
        if results.errors:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("⚠️ <b>สถานะเพิ่มเติม</b>")
            for error in results.errors:
                lines.append(f"  • {html.escape(error.source)}: {html.escape(error.message)}")
        return "\n".join(lines).strip()

    async def _render_facebook(self, results: CollectionResult) -> list[str]:
        lines = ["📘 <b>Facebook</b>", "─────────────────────"]
        if not results.facebook_posts:
            lines.append("  ℹ️ ไม่พบข้อมูลจาก Facebook")
            return lines

        for index, post in enumerate(results.facebook_posts, start=1):
            text_preview = html.escape((post.post_text or "")[:120])
            if post.post_text and len(post.post_text) > 120:
                text_preview += "…"
            lines.append(f"\n<b>{index}.</b> {text_preview}")

            post_url = post.post_url or ""
            if post_url:
                short = await _shorten_url(post_url)
                lines.append(f"   📎 <a href=\"{html.escape(short)}\">ดูโพสต์</a>")
            else:
                lines.append("   📎 ไม่มีลิงก์โพสต์")

            if post.comments:
                comment = post.comments[0]
                ctext = html.escape((comment.text or "")[:100])
                lines.append(f"   💬 <i>\"{ctext}\"</i>")
                if comment.comment_url:
                    short_c = await _shorten_url(comment.comment_url)
                    lines.append(f"   🔗 <a href=\"{html.escape(short_c)}\">ดูคอมเมนต์</a>")
                else:
                    lines.append("   🔗 ไม่มีลิงก์คอมเมนต์")
            else:
                lines.append("   💬 ไม่พบคอมเมนต์")
        return lines

    async def _render_google(self, results: CollectionResult) -> list[str]:
        lines = ["🌐 <b>Google</b>", "─────────────────────"]
        if not results.google_results:
            lines.append("  ℹ️ ไม่พบข้อมูลจาก Google")
            return lines

        for index, item in enumerate(results.google_results, start=1):
            source = html.escape(item.source or urlparse(item.url).netloc)
            title = html.escape(item.title or "(ไม่มีชื่อ)")
            snippet = html.escape((item.snippet or "ไม่มี snippet")[:150])

            lines.append(f"\n<b>{index}. {title}</b>")
            lines.append(f"   🏷 {source}")
            lines.append(f"   📝 {snippet}")
            if item.url:
                short = await _shorten_url(item.url)
                lines.append(f"   🔗 <a href=\"{html.escape(short)}\">อ่านต่อ</a>")
        return lines