from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


ParseMode = Literal["HTML"] | None
LinkStyle = Literal["buttons", "text"]
ScheduledPostStatus = Literal["pending", "processing", "published", "failed", "canceled"]
ModerationDraftStatus = Literal[
    "pending_review",
    "awaiting_schedule",
    "awaiting_rejection_comment",
    "scheduled",
    "published",
    "rejected",
    "failed",
]
ArticleStatus = Literal[
    "draft",
    "pending_review",
    "awaiting_schedule",
    "scheduled",
    "rejected",
    "published",
    "failed",
]


class LinkItem(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    url: AnyHttpUrl


class ImageItem(BaseModel):
    url: AnyHttpUrl | None = None
    path: Path | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "ImageItem":
        if self.url is None and self.path is None:
            raise ValueError("ImageItem requires either url or path.")
        if self.url is not None and self.path is not None:
            raise ValueError("ImageItem accepts only one source: url or path.")
        return self


class PublishRequest(BaseModel):
    text: str = Field(default="")
    parse_mode: ParseMode = "HTML"
    images: list[ImageItem] = Field(default_factory=list, max_length=10)
    links: list[LinkItem] = Field(default_factory=list, max_length=10)
    link_style: LinkStyle | None = None
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    protect_content: bool = False
    dry_run: bool = False
    chat_id: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "PublishRequest":
        if not self.text.strip() and not self.images and not self.links:
            raise ValueError("Request must include text, images, or links.")
        if self.parse_mode == "HTML" and len(self.text) > 4096:
            raise ValueError("HTML messages support up to 4096 characters.")
        return self


class PublishResponse(BaseModel):
    ok: bool
    strategy: str
    rendered_text: str
    actions: list[dict[str, Any]]
    telegram_results: list[dict[str, Any]] = Field(default_factory=list)


class SchedulePublishRequest(PublishRequest):
    publish_at: datetime

    @model_validator(mode="after")
    def validate_schedule_request(self) -> "SchedulePublishRequest":
        if self.dry_run:
            raise ValueError("Scheduled posts do not support dry_run.")
        if self.publish_at.tzinfo is None or self.publish_at.utcoffset() is None:
            raise ValueError("publish_at must include timezone information.")
        if self.publish_at.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("publish_at must be in the future.")
        return self


class SubmitDraftRequest(PublishRequest):
    @model_validator(mode="after")
    def validate_submit_request(self) -> "SubmitDraftRequest":
        if self.dry_run:
            raise ValueError("Draft submission does not support dry_run.")
        return self


class ArticleSnapshotRequest(BaseModel):
    article_id: str | None = None
    title: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    scheduled_publish_at: datetime | None = None
    moderation_comment: str | None = None
    cover_path: str | None = None
    payload_path: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    attached_links: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    publish_strategy: str | None = None
    last_error: str | None = None
    payload: PublishRequest

    @model_validator(mode="after")
    def validate_article_request(self) -> "ArticleSnapshotRequest":
        if self.scheduled_publish_at is not None:
            if (
                self.scheduled_publish_at.tzinfo is None
                or self.scheduled_publish_at.utcoffset() is None
            ):
                raise ValueError("scheduled_publish_at must include timezone information.")
        return self


class ScheduledPostResponse(BaseModel):
    id: str
    status: ScheduledPostStatus
    publish_at: datetime
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None
    request: PublishRequest
    last_result: dict[str, Any] | None = None


class ModerationDraftResponse(BaseModel):
    id: str
    article_id: str | None = None
    status: ModerationDraftStatus
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    rejected_at: datetime | None = None
    scheduled_publish_at: datetime | None = None
    scheduled_post_id: str | None = None
    last_error: str | None = None
    request: PublishRequest
    publication_result: dict[str, Any] | None = None


class ArticleResponse(BaseModel):
    article_id: str
    status: ArticleStatus
    created_at: datetime
    updated_at: datetime
    scheduled_publish_at: datetime | None = None
    published_at: datetime | None = None
    moderation_comment: str | None = None
    title: str
    slug: str
    cover_path: str | None = None
    payload_path: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    attached_links: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    publish_strategy: str | None = None
    last_error: str | None = None
    markdown: str
    payload: PublishRequest
    current_draft_id: str | None = None


class ArticleDryRunResponse(BaseModel):
    article: ArticleResponse
    dry_run: PublishResponse


class ArticleSubmitResponse(BaseModel):
    article: ArticleResponse
    draft: ModerationDraftResponse


class ArticleCommentResponse(BaseModel):
    id: str
    draft_id: str
    article_id: str | None = None
    body: str
    moderator_user_id: int
    created_at: datetime
    applied_at: datetime | None = None
