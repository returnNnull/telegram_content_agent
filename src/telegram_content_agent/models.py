from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


ParseMode = Literal["HTML"] | None
LinkStyle = Literal["buttons", "text"]


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
    text: str = Field(default="", max_length=4096)
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
        return self


class PublishResponse(BaseModel):
    ok: bool
    strategy: str
    rendered_text: str
    actions: list[dict]
    telegram_results: list[dict] = Field(default_factory=list)
