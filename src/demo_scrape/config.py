from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def load_dotenv(dotenv_path: str = ".env", *, override: bool = False) -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class AppConfig:
    telegram_bot_token: str | None
    ollama_host: str
    ollama_model: str
    facebook_profile_dir: str | None
    browser_channel: str
    headless: bool
    max_facebook_posts: int
    max_google_results: int
    default_max_comments_per_post: int
    facebook_scroll_rounds: int
    request_timeout_seconds: int
    telegram_message_limit: int
    use_stub_results: bool
    relevance_threshold: float
    min_relevant_results: int
    collection_overfetch_multiplier: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv(override=False)
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            ollama_host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "hermes"),
            facebook_profile_dir=os.getenv("FACEBOOK_PROFILE_DIR"),
            browser_channel=os.getenv("BROWSER_CHANNEL", "chrome"),
            headless=_env_bool("HEADLESS", True),
            max_facebook_posts=_env_int("MAX_FACEBOOK_POSTS", 3),
            max_google_results=_env_int("MAX_GOOGLE_RESULTS", 5),
            default_max_comments_per_post=_env_int("MAX_COMMENTS_PER_POST", 3),
            facebook_scroll_rounds=_env_int("FACEBOOK_SCROLL_ROUNDS", 50),
            request_timeout_seconds=_env_int("REQUEST_TIMEOUT_SECONDS", 20),
            telegram_message_limit=_env_int("TELEGRAM_MESSAGE_LIMIT", 3500),
            use_stub_results=_env_bool("USE_STUB_RESULTS", False),
            relevance_threshold=float(os.getenv("RELEVANCE_THRESHOLD", "0.4")),
            min_relevant_results=_env_int("MIN_RELEVANT_RESULTS", 2),
            collection_overfetch_multiplier=_env_int("COLLECTION_OVERFETCH_MULTIPLIER", 2),
        )

    def missing_required_for_bot(self) -> list[str]:
        missing: list[str] = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        return missing

    def missing_required_for_facebook(self) -> list[str]:
        missing: list[str] = []
        if not self.facebook_profile_dir:
            missing.append("FACEBOOK_PROFILE_DIR")
        return missing