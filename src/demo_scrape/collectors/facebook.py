from __future__ import annotations

from pathlib import Path
import logging
import math
import re
import shutil
import tempfile
from urllib.parse import quote_plus, urljoin

from demo_scrape.collectors.base import Collector
from demo_scrape.config import AppConfig
from demo_scrape.models import FacebookCommentResult, FacebookPostResult, SearchPlan

_log = logging.getLogger(__name__)


class FacebookCollector(Collector):
    source_name = "facebook"

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def collect(self, plan: SearchPlan) -> list[FacebookPostResult]:
        if self._config.use_stub_results:
            _log.info("Facebook: using stub results | keyword=%r", plan.keyword)
            return _stub_posts(plan.keyword, plan.max_facebook_posts)

        if not self._config.facebook_profile_dir:
            raise RuntimeError("FACEBOOK_PROFILE_DIR is required for Facebook collection")

        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed") from exc

        user_data_dir, profile_args = _resolve_chrome_profile(self._config.facebook_profile_dir)
        staged_user_data_dir = _prepare_automation_user_data_dir(user_data_dir, profile_args)

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                staged_user_data_dir,
                channel=self._config.browser_channel,
                headless=self._config.headless,
                args=profile_args,
                viewport={"width": 1440, "height": 900},
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                search_url = f"https://www.facebook.com/search/posts?q={quote_plus(plan.keyword)}"
                _log.info("Facebook: navigating to search | url=%s", search_url)
                await page.goto(search_url, wait_until="domcontentloaded")
                if await page.locator("input[name='email']").count():
                    raise RuntimeError("Facebook session is not logged in for the configured Chrome profile")
                _log.debug("Facebook: waiting for articles")
                await page.wait_for_selector("[role='article']", timeout=self._config.request_timeout_seconds * 1000)
                posts = await self._extract_posts(page, plan)
            except PlaywrightTimeoutError as exc:
                raise RuntimeError("Timed out waiting for Facebook results") from exc
            finally:
                await context.close()

        if not posts:
            raise RuntimeError("No Facebook posts were extracted")

        _log.info("Facebook: collected %d posts | keyword=%r", len(posts), plan.keyword)
        return posts

    async def _extract_posts(self, page, plan: SearchPlan) -> list[FacebookPostResult]:
        # Clean up screenshots from previous run.
        screenshot_dir = Path(tempfile.gettempdir()) / "demo-scrape-screenshots"
        if screenshot_dir.exists():
            shutil.rmtree(screenshot_dir, ignore_errors=True)

        posts: list[FacebookPostResult] = []
        seen_texts: set[str] = set()

        # First pass: collect posts without comments so we never navigate away from
        # the search results page during this phase.
        max_rounds = self._config.facebook_scroll_rounds  # safety cap
        stale_rounds = 0
        prev_article_count = 0
        round_num = 0
        while round_num < max_rounds:
            articles = page.locator("[role='article']")
            count = await articles.count()
            _log.debug("Facebook scroll round %d/%d | articles_visible=%d",
                       round_num + 1, max_rounds, count)

            for index in range(count):
                article = articles.nth(index)
                post_text = _normalize_whitespace(await article.inner_text())
                if not post_text or post_text in seen_texts:
                    continue

                seen_texts.add(post_text)
                post_url = await _find_post_url(article)
                screenshot_path = await _screenshot_article(article, len(posts))
                author = await _find_author(article)
                posts.append(
                    FacebookPostResult(
                        author=author,
                        post_text=post_text,
                        post_url=post_url,
                        screenshot_path=screenshot_path,
                        comments=[],
                    )
                )
                _log.debug("Facebook: post collected | author=%r  url=%s", author, post_url)

                if len(posts) >= plan.max_facebook_posts:
                    break

            if len(posts) >= plan.max_facebook_posts:
                _log.debug("Facebook: reached max_facebook_posts=%d, stopping scroll", plan.max_facebook_posts)
                break

            # Stale detection: stop if DOM article count hasn't grown for 3 rounds
            if count <= prev_article_count:
                stale_rounds += 1
                if stale_rounds >= 3:
                    _log.debug("Facebook: content exhausted after %d stale rounds, stopping", stale_rounds)
                    break
            else:
                stale_rounds = 0

            prev_article_count = count
            round_num += 1
            await page.keyboard.press("End")
            await page.wait_for_timeout(1500)

        # Second pass: fetch comments via a new tab so the search page stays intact.
        _log.info("Facebook: fetching comments for %d posts", len(posts))
        for i, post in enumerate(posts):
            if not post.post_url:
                continue
            try:
                tab = await page.context.new_page()
                _log.debug("Facebook: fetching comments post %d/%d | url=%s", i + 1, len(posts), post.post_url)
                post.comments = await _fetch_comments_from_post_page(
                    tab, post.post_url, plan.max_comments_per_post
                )
                _log.debug("Facebook: got %d comments for post %d", len(post.comments), i + 1)
                await tab.close()
            except Exception as exc:
                _log.debug("Facebook: comment fetch failed for post %d: %s", i + 1, exc)

        return posts


async def _find_post_url(article) -> str | None:
    anchors = article.locator("a[href]")
    count = min(await anchors.count(), 25)
    for index in range(count):
        href = await anchors.nth(index).get_attribute("href")
        if not href:
            continue
        absolute = urljoin("https://www.facebook.com", href)
        if any(token in absolute for token in ("/posts/", "/permalink/", "story.php", "/groups/")):
            return absolute
    return None


async def _screenshot_article(article, index: int) -> str | None:
    screenshot_dir = Path(tempfile.gettempdir()) / "demo-scrape-screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = str(screenshot_dir / f"post-{index}.png")
    try:
        # Scroll element into view so lazy-loaded images inside it are triggered.
        await article.scroll_into_view_if_needed(timeout=3000)
        # Wait briefly for images inside the article to finish loading.
        await article.page.wait_for_timeout(800)
        # element screenshot captures the full bounding box, not clipped to viewport.
        await article.screenshot(path=path)
        return path
    except Exception:
        return None


async def _find_author(article) -> str | None:
    candidate = article.locator("h2 a, h3 a, strong a")
    if await candidate.count():
        return _normalize_whitespace(await candidate.first.inner_text())
    return None


async def _fetch_comments_from_post_page(tab, post_url: str, max_comments: int = 3) -> list[FacebookCommentResult]:
    """Open the post's permalink in a new tab and extract comments from it."""
    await tab.goto(post_url, wait_until="domcontentloaded")
    try:
        await tab.wait_for_selector("[role='article']", timeout=12000)
    except Exception:
        return []
    await tab.wait_for_timeout(1500)

    # Load more comments by clicking "ดูความคิดเห็นเพิ่มเติม" / "View more comments"
    max_clicks = math.ceil(max_comments / 3)
    load_more_selectors = [
        "[aria-label*='ความคิดเห็นเพิ่มเติม']",
        "[aria-label*='View more comments']",
        "div[role='button']:has-text('ดูความคิดเห็นเพิ่มเติม')",
        "div[role='button']:has-text('View more comments')",
    ]
    prev_count = 0
    for _ in range(max_clicks):
        # Count current visible comment articles before clicking
        cur_count = await tab.locator("[role='article'][aria-label*='ความคิดเห็น']").count()
        if cur_count == 0:
            cur_count = await tab.locator("[role='article']").count()
        if cur_count >= max_comments:
            break
        clicked = False
        for sel in load_more_selectors:
            btn = tab.locator(sel).first
            if await btn.count() > 0:
                try:
                    await btn.click()
                    await tab.wait_for_timeout(1500)
                    clicked = True
                except Exception:
                    pass
                break
        if not clicked:
            break
        new_count = await tab.locator("[role='article'][aria-label*='ความคิดเห็น']").count()
        if new_count == 0:
            new_count = await tab.locator("[role='article']").count()
        if new_count <= prev_count:
            break
        prev_count = new_count

    comments: list[FacebookCommentResult] = []
    seen_texts: set[str] = set()

    # On Facebook permalink pages, individual comments are [role='article'] elements
    # that have an aria-label containing 'ความคิดเห็น' (comment).
    # Fall back to scanning ALL articles after index 0 (the main post) if needed.
    comment_articles = tab.locator("[role='article'][aria-label*='ความคิดเห็น']")
    n_count = await comment_articles.count()

    if n_count == 0:
        # Fallback: scan all articles, skip index 0 (the main post)
        comment_articles = tab.locator("[role='article']")
        start_index = 1
    else:
        start_index = 0

    n_count = min(await comment_articles.count(), 20)
    for index in range(start_index, n_count):
        node = comment_articles.nth(index)
        text = _normalize_whitespace(await node.inner_text())
        # Strip trailing Facebook UI metadata (e.g. "1 ชั่วโมง ถูกใจ ตอบกลับ")
        # Requires a UI action word (ถูกใจ/ตอบกลับ/แก้ไขแล้ว) after the time unit
        # so legitimate content like "ใช้เวลา 2 ชั่วโมง แล้วเสร็จ" is NOT stripped.
        text = re.sub(
            r"\s*\d+\s*(?:ชั่วโมง|นาที|วัน|สัปดาห์|เดือน)\s*(?:ถูกใจ|ตอบกลับ|แก้ไขแล้ว).*$",
            "",
            text,
        ).strip()
        if not text:
            continue
        # Dedup using whitespace-collapsed key to catch both spaced/unspaced variants
        dedup_key = re.sub(r"\s+", "", text)
        if dedup_key in seen_texts:
            continue
        if len(text) < 6 or len(text) > 600:
            continue
        if re.search(r"^(ถูกใจ|แสดงความคิดเห็น|แชร์|ตอบกลับ|เขียนความคิดเห็น|เขียน)", text):
            continue
        seen_texts.add(dedup_key)
        comment_url = None
        anchors = node.locator("a[href]")
        if await anchors.count():
            href = await anchors.first.get_attribute("href")
            if href:
                absolute = urljoin("https://www.facebook.com", href)
                if re.search(r"comment|reply|story\.php|permalink", absolute):
                    comment_url = absolute
        comments.append(FacebookCommentResult(author=None, text=text, comment_url=comment_url))
        if len(comments) >= max_comments:
            break

    return comments


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _stub_posts(keyword: str, limit: int) -> list[FacebookPostResult]:
    posts = [
        FacebookPostResult(
            author="Demo Page",
            post_text=f"โพสต์ตัวอย่างเกี่ยวกับ {keyword} สำหรับทดสอบ pipeline",
            post_url=f"https://www.facebook.com/demo/posts/{quote_plus(keyword)}-1",
            screenshot_path=None,
            comments=[
                FacebookCommentResult(
                    author="User A",
                    text=f"คอมเมนต์ตัวอย่างเกี่ยวกับ {keyword}",
                    comment_url=f"https://www.facebook.com/demo/comments/{quote_plus(keyword)}-1",
                )
            ],
        ),
        FacebookPostResult(
            author="Demo Community",
            post_text=f"สรุปประเด็นที่คนพูดถึง {keyword} ในโพสต์ตัวอย่าง",
            post_url=f"https://www.facebook.com/demo/posts/{quote_plus(keyword)}-2",
            screenshot_path=None,
            comments=[],
        ),
    ]
    return posts[:limit]


def _resolve_chrome_profile(configured_path: str) -> tuple[str, list[str]]:
    path = Path(configured_path).expanduser()
    profile_name = path.name
    if profile_name == "Default" or profile_name.startswith("Profile "):
        return str(path.parent), [f"--profile-directory={profile_name}"]
    return str(path), []


def _prepare_automation_user_data_dir(user_data_dir: str, profile_args: list[str]) -> str:
    source_root = Path(user_data_dir)
    if not source_root.exists():
        return user_data_dir

    staged_root = Path(tempfile.gettempdir()) / "demo-scrape-chrome-user-data"

    # Always refresh the staged copy so Playwright uses the latest login session.
    if staged_root.exists():
        shutil.rmtree(staged_root, ignore_errors=True)
    staged_root.mkdir(parents=True, exist_ok=True)

    profile_name = _profile_name_from_args(profile_args)
    if profile_name:
        source_profile = source_root / profile_name
        staged_profile = staged_root / profile_name
        if source_profile.exists():
            shutil.copytree(
                source_profile, staged_profile,
                ignore=shutil.ignore_patterns("Singleton*", "LOCK", "lockfile"),
            )
        local_state = source_root / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, staged_root / "Local State")
    else:
        for child in source_root.iterdir():
            if child.name.startswith("Singleton"):
                continue
            target = staged_root / child.name
            if child.is_dir():
                shutil.copytree(child, target, ignore=shutil.ignore_patterns("Singleton*", "LOCK", "lockfile"))
            else:
                shutil.copy2(child, target)

    # Remove any lock files that might block Chrome from starting.
    for lock_path in staged_root.rglob("Singleton*"):
        lock_path.unlink(missing_ok=True)
    for lock_path in staged_root.rglob("LOCK"):
        lock_path.unlink(missing_ok=True)

    return str(staged_root)


def _profile_name_from_args(profile_args: list[str]) -> str | None:
    prefix = "--profile-directory="
    for arg in profile_args:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None