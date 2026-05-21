from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class SearchPlan:
    raw_question: str
    keyword: str
    sources: tuple[str, ...]
    max_facebook_posts: int
    max_google_results: int
    max_comments_per_post: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class FacebookCommentResult:
    author: str | None
    text: str
    comment_url: str | None = None


@dataclass(slots=True)
class FacebookPostResult:
    author: str | None
    post_text: str
    post_url: str | None = None
    screenshot_path: str | None = None
    comments: list[FacebookCommentResult] = field(default_factory=list)
    relevance_score: float = 0.0


@dataclass(slots=True)
class GoogleResult:
    title: str
    snippet: str
    source: str
    url: str
    relevance_score: float = 0.0


@dataclass(slots=True)
class SourceError:
    source: str
    message: str


@dataclass(slots=True)
class CollectionResult:
    facebook_posts: list[FacebookPostResult] = field(default_factory=list)
    google_results: list[GoogleResult] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)