from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from html import escape

from demo_scrape.config import AppConfig
from demo_scrape.orchestrator import DemoOrchestrator
from demo_scrape.planner import build_search_plan

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-user session state
# ---------------------------------------------------------------------------

@dataclass
class UserSession:
    pending_question: str = ""
    source_pref: str = "both"          # "facebook" | "google" | "both"
    max_posts_pref: int = 10
    max_comments_pref: int = 3
    seen_fb_urls: set[str] = field(default_factory=set)
    seen_google_urls: set[str] = field(default_factory=set)


_user_sessions: dict[int, UserSession] = {}


def _get_session(user_id: int) -> UserSession:
    if user_id not in _user_sessions:
        _user_sessions[user_id] = UserSession()
    return _user_sessions[user_id]


def _reset_session(user_id: int) -> UserSession:
    _user_sessions[user_id] = UserSession()
    return _user_sessions[user_id]


# ---------------------------------------------------------------------------
# Inline keyboard builder
# ---------------------------------------------------------------------------

def _build_settings_keyboard(session: UserSession):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    def mark(label: str, active: bool) -> str:
        return f"✓ {label}" if active else label

    src_row = [
        InlineKeyboardButton(mark("Facebook", session.source_pref == "facebook"), callback_data="src:facebook"),
        InlineKeyboardButton(mark("Google", session.source_pref == "google"), callback_data="src:google"),
        InlineKeyboardButton(mark("ทั้งคู่", session.source_pref == "both"), callback_data="src:both"),
    ]
    posts_row = [
        InlineKeyboardButton(mark("5 โพส", session.max_posts_pref == 5), callback_data="posts:5"),
        InlineKeyboardButton(mark("10 โพส", session.max_posts_pref == 10), callback_data="posts:10"),
        InlineKeyboardButton(mark("20 โพส", session.max_posts_pref == 20), callback_data="posts:20"),
    ]
    cmts_row = [
        InlineKeyboardButton(mark("3 ความเห็น", session.max_comments_pref == 3), callback_data="cmts:3"),
        InlineKeyboardButton(mark("5 ความเห็น", session.max_comments_pref == 5), callback_data="cmts:5"),
        InlineKeyboardButton(mark("10 ความเห็น", session.max_comments_pref == 10), callback_data="cmts:10"),
    ]
    confirm_row = [
        InlineKeyboardButton("🔍 ค้นหาเลย", callback_data="confirm"),
    ]
    return InlineKeyboardMarkup([src_row, posts_row, cmts_row, confirm_row])


def _settings_summary(session: UserSession) -> str:
    source_label = {"facebook": "Facebook", "google": "Google", "both": "Facebook + Google"}[session.source_pref]
    return (
        f"📋 <b>ตั้งค่าการค้นหา</b>\n"
        f"❓ <i>{escape(session.pending_question)}</i>\n\n"
        f"แหล่งข้อมูล · จำนวนโพส · ความเห็น/โพส\n"
        f"กดเพื่อเปลี่ยน แล้วกด <b>🔍 ค้นหาเลย</b>"
    )


# ---------------------------------------------------------------------------
# URL shortener
# ---------------------------------------------------------------------------

async def _shorten_url(url: str) -> str:
    if not url:
        return url
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://is.gd/create.php",
                params={"format": "simple", "url": url},
            )
            if resp.status_code == 200 and resp.text.startswith("http"):
                return resp.text.strip()
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# Result rendering helpers
# ---------------------------------------------------------------------------

async def _build_post_caption(post) -> str:
    parts: list[str] = []
    if post.author:
        parts.append(f"<b>{escape(post.author)}</b>")
    text = post.post_text
    if len(text) > 500:
        text = text[:500] + "…"
    parts.append(escape(text))
    if post.comments:
        parts.append("")
        parts.append("💬 <b>ความคิดเห็น</b>")
        for c in post.comments[:10]:
            parts.append(f"• {escape(c.text[:180])}")
    if post.post_url:
        short = await _shorten_url(post.post_url)
        parts.append(f"\n🔗 {short}")
    caption = "\n".join(parts)
    if len(caption) > 1020:
        caption = caption[:1020] + "…"
    return caption


async def _build_google_block(google_results) -> str:
    lines: list[str] = ["🔍 <b>Google</b>"]
    for i, r in enumerate(google_results, 1):
        short = await _shorten_url(r.url)
        lines.append(f"\n{i}. <b>{escape(r.title)}</b>")
        if r.snippet:
            lines.append(escape(r.snippet[:220]))
        lines.append(f"🔗 {short}")
    return "\n".join(lines)


async def _send_results(message, rendered: str, results, config: AppConfig) -> None:
    for chunk in _split_message(rendered, config.telegram_message_limit):
        await message.reply_text(chunk, disable_web_page_preview=True)

    for post in results.facebook_posts:
        caption = await _build_post_caption(post)
        sent_photo = False
        if post.screenshot_path:
            try:
                with open(post.screenshot_path, "rb") as f:
                    await message.reply_photo(f, caption=caption, parse_mode="HTML")
                os.unlink(post.screenshot_path)
                sent_photo = True
            except Exception:
                pass
        if not sent_photo:
            await message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=True)

    if results.google_results:
        google_block = await _build_google_block(results.google_results)
        for chunk in _split_message(google_block, config.telegram_message_limit):
            await message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Bot entrypoint
# ---------------------------------------------------------------------------

def run_polling_bot(config: AppConfig) -> None:
    missing = config.missing_required_for_bot()
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError as exc:
        raise RuntimeError("python-telegram-bot is not installed") from exc

    orchestrator = DemoOrchestrator(config)

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        _log.info("Bot /start | user_id=%d", user_id)
        _reset_session(user_id)
        await update.message.reply_text("พร้อมรับคำถามแล้ว (session รีเซ็ตแล้ว)")

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        user_id = update.effective_user.id
        session = _get_session(user_id)
        session.pending_question = update.message.text.strip()
        _log.info("Bot message | user_id=%d  question=%r", user_id, session.pending_question[:80])
        keyboard = _build_settings_keyboard(session)
        await update.message.reply_text(
            _settings_summary(session),
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        session = _get_session(user_id)
        data = query.data or ""

        if data.startswith("src:"):
            session.source_pref = data[4:]
            await query.edit_message_reply_markup(_build_settings_keyboard(session))
            return

        if data.startswith("posts:"):
            session.max_posts_pref = int(data[6:])
            await query.edit_message_reply_markup(_build_settings_keyboard(session))
            return

        if data.startswith("cmts:"):
            session.max_comments_pref = int(data[5:])
            await query.edit_message_reply_markup(_build_settings_keyboard(session))
            return

        if data == "confirm":
            if not session.pending_question:
                await query.edit_message_text("❌ ไม่พบคำถาม กรุณาพิมพ์คำถามใหม่")
                return

            source_map = {
                "facebook": ("facebook",),
                "google": ("google",),
                "both": ("facebook", "google"),
            }
            sources = source_map[session.source_pref]
            source_label = {"facebook": "Facebook", "google": "Google", "both": "Facebook + Google"}[session.source_pref]
            _log.info("Bot confirm search | user_id=%d  question=%r  sources=%s",
                      user_id, session.pending_question[:80], session.source_pref)

            status_text = [
                f"🔍 กำลังค้นหา: <b>{escape(session.pending_question)}</b>\n"
                f"แหล่ง: {source_label} · โพส: {session.max_posts_pref} · ความเห็น: {session.max_comments_pref}/โพส"
            ]
            await query.edit_message_text(status_text[0], parse_mode="HTML")

            async def _progress(msg: str) -> None:
                """Edit the status message so users see live progress."""
                status_text[0] = (
                    f"🔍 <b>{escape(session.pending_question)}</b>\n"
                    f"{escape(msg)}"
                )
                try:
                    await query.edit_message_text(status_text[0], parse_mode="HTML")
                except Exception:
                    pass

            plan = build_search_plan(
                session.pending_question,
                config,
                sources_override=sources,
                max_posts_override=session.max_posts_pref,
                max_comments_override=session.max_comments_pref,
            )

            try:
                _, results, rendered = await orchestrator.handle_question(
                    session.pending_question,
                    plan=plan,
                    seen_fb_urls=session.seen_fb_urls,
                    seen_google_urls=session.seen_google_urls,
                    progress_callback=_progress,
                )
            except Exception as exc:
                _log.error("Bot orchestrator error | user_id=%d  error=%s", user_id, exc)
                await query.message.reply_text(f"ระบบประมวลผลไม่สำเร็จ: {exc}")
                return

            # Update session seen sets so next query won't repeat
            for post in results.facebook_posts:
                if post.post_url:
                    session.seen_fb_urls.add(post.post_url)
            for r in results.google_results:
                session.seen_google_urls.add(r.url)

            _log.info("Bot sending results | user_id=%d  fb=%d  google=%d",
                      user_id, len(results.facebook_posts), len(results.google_results))
            await _send_results(query.message, rendered, results, config)

    application = Application.builder().token(config.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.run_polling()


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))
    return chunks

