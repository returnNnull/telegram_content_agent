from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from telegram_content_agent.config import Settings
from telegram_content_agent.models import (
    ArticleCommentResponse,
    ArticleResponse,
    PublishRequest,
)


class ArticleNotFoundError(RuntimeError):
    """Raised when an article cannot be found."""


@dataclass(slots=True)
class ArticleRecord:
    id: str
    source_path: Path
    publication: str
    slug: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    current_source_hash: str
    last_submitted_hash: str | None
    current_draft_id: str | None
    published_at: datetime | None
    last_error: str | None

    def to_response(self) -> ArticleResponse:
        return ArticleResponse(
            id=self.id,
            source_path=self.source_path,
            publication=self.publication,
            slug=self.slug,
            title=self.title,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            current_source_hash=self.current_source_hash,
            last_submitted_hash=self.last_submitted_hash,
            current_draft_id=self.current_draft_id,
            published_at=self.published_at,
            last_error=self.last_error,
        )


@dataclass(slots=True)
class ArticleCommentRecord:
    id: str
    draft_id: str
    article_id: str | None
    body: str
    moderator_user_id: int
    created_at: datetime
    applied_at: datetime | None

    def to_response(self) -> ArticleCommentResponse:
        return ArticleCommentResponse(
            id=self.id,
            draft_id=self.draft_id,
            article_id=self.article_id,
            body=self.body,
            moderator_user_id=self.moderator_user_id,
            created_at=self.created_at,
            applied_at=self.applied_at,
        )


@dataclass(slots=True)
class ArticleSource:
    id: str
    path: Path
    publication: str
    slug: str
    title: str
    markdown_text: str
    rendered_text: str
    content_hash: str


class ArticleStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        if not self._db_path.is_absolute():
            self._db_path = self._db_path.resolve()

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL UNIQUE,
                    publication TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    current_source_hash TEXT NOT NULL,
                    last_submitted_hash TEXT,
                    current_draft_id TEXT,
                    published_at TEXT,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS article_comments (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    article_id TEXT,
                    body TEXT NOT NULL,
                    moderator_user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_article_comments_article_id
                ON article_comments(article_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_article_comments_draft_id
                ON article_comments(draft_id, created_at DESC)
                """
            )
            connection.commit()

    def upsert_source(self, source: ArticleSource) -> ArticleRecord:
        now = self._iso(datetime.now(UTC))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM articles WHERE id = ?",
                (source.id,),
            ).fetchone()
            payload = (
                str(source.path),
                source.publication,
                source.slug,
                source.title,
                source.content_hash,
                now,
                source.id,
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO articles (
                        id, source_path, publication, slug, title, status,
                        created_at, updated_at, current_source_hash, last_submitted_hash,
                        current_draft_id, published_at, last_error
                    ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        source.id,
                        str(source.path),
                        source.publication,
                        source.slug,
                        source.title,
                        now,
                        now,
                        source.content_hash,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE articles
                    SET source_path = ?,
                        publication = ?,
                        slug = ?,
                        title = ?,
                        current_source_hash = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    payload,
                )
            connection.commit()
        return self.get_record(source.id)

    def attach_draft(
        self,
        article_id: str,
        *,
        draft_id: str,
        source_hash: str,
        status: str,
    ) -> ArticleRecord:
        return self._update_article(
            article_id,
            """
            UPDATE articles
            SET current_draft_id = ?,
                last_submitted_hash = ?,
                status = ?,
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (draft_id, source_hash, status, self._iso(datetime.now(UTC)), article_id),
        )

    def sync_status(
        self,
        article_id: str,
        *,
        status: str,
        published_at: datetime | None = None,
        last_error: str | None = None,
    ) -> ArticleRecord:
        return self._update_article(
            article_id,
            """
            UPDATE articles
            SET status = ?,
                updated_at = ?,
                published_at = COALESCE(?, published_at),
                last_error = ?
            WHERE id = ?
            """,
            (
                status,
                self._iso(datetime.now(UTC)),
                self._iso(published_at) if published_at is not None else None,
                last_error,
                article_id,
            ),
        )

    def list(self, *, status: str | None = None) -> list[ArticleResponse]:
        query = "SELECT * FROM articles"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_article(row).to_response() for row in rows]

    def get(self, article_id: str) -> ArticleResponse:
        return self.get_record(article_id).to_response()

    def get_record(self, article_id: str) -> ArticleRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
        if row is None:
            raise ArticleNotFoundError(f"Article not found: {article_id}")
        return self._row_to_article(row)

    def create_comment(
        self,
        *,
        draft_id: str,
        article_id: str | None,
        body: str,
        moderator_user_id: int,
    ) -> ArticleCommentRecord:
        now = datetime.now(UTC)
        comment_id = uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO article_comments (
                    id, draft_id, article_id, body, moderator_user_id, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    comment_id,
                    draft_id,
                    article_id,
                    body,
                    moderator_user_id,
                    self._iso(now),
                ),
            )
            connection.commit()
        return self.get_comment(comment_id)

    def get_comment(self, comment_id: str) -> ArticleCommentRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM article_comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        if row is None:
            raise ArticleNotFoundError(f"Comment not found: {comment_id}")
        return self._row_to_comment(row)

    def list_comments(
        self,
        *,
        article_id: str | None = None,
        draft_id: str | None = None,
    ) -> list[ArticleCommentResponse]:
        query = "SELECT * FROM article_comments"
        params: list[str] = []
        clauses: list[str] = []
        if article_id is not None:
            clauses.append("article_id = ?")
            params.append(article_id)
        if draft_id is not None:
            clauses.append("draft_id = ?")
            params.append(draft_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_comment(row).to_response() for row in rows]

    def latest_comment(
        self,
        *,
        article_id: str | None = None,
        draft_id: str | None = None,
        unapplied_only: bool = False,
    ) -> ArticleCommentRecord | None:
        clauses: list[str] = []
        params: list[str] = []
        if article_id is not None:
            clauses.append("article_id = ?")
            params.append(article_id)
        if draft_id is not None:
            clauses.append("draft_id = ?")
            params.append(draft_id)
        if unapplied_only:
            clauses.append("applied_at IS NULL")
        query = "SELECT * FROM article_comments"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        return self._row_to_comment(row)

    def mark_comment_applied(self, comment_id: str) -> ArticleCommentRecord:
        return self._update_comment(
            comment_id,
            """
            UPDATE article_comments
            SET applied_at = ?
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), comment_id),
        )

    def count_draft_attempts(self, article_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM moderation_drafts WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        if row is None:
            return 0
        return int(row["total"])

    def _update_article(
        self,
        article_id: str,
        statement: str,
        params: tuple[object, ...],
    ) -> ArticleRecord:
        with self._connect() as connection:
            updated = connection.execute(statement, params)
            if updated.rowcount == 0:
                raise ArticleNotFoundError(f"Article not found: {article_id}")
            connection.commit()
        return self.get_record(article_id)

    def _update_comment(
        self,
        comment_id: str,
        statement: str,
        params: tuple[object, ...],
    ) -> ArticleCommentRecord:
        with self._connect() as connection:
            updated = connection.execute(statement, params)
            if updated.rowcount == 0:
                raise ArticleNotFoundError(f"Comment not found: {comment_id}")
            connection.commit()
        return self.get_comment(comment_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_article(self, row: sqlite3.Row) -> ArticleRecord:
        return ArticleRecord(
            id=row["id"],
            source_path=Path(row["source_path"]),
            publication=row["publication"],
            slug=row["slug"],
            title=row["title"],
            status=row["status"],
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
            current_source_hash=row["current_source_hash"],
            last_submitted_hash=row["last_submitted_hash"],
            current_draft_id=row["current_draft_id"],
            published_at=self._parse_optional_datetime(row["published_at"]),
            last_error=row["last_error"],
        )

    def _row_to_comment(self, row: sqlite3.Row) -> ArticleCommentRecord:
        return ArticleCommentRecord(
            id=row["id"],
            draft_id=row["draft_id"],
            article_id=row["article_id"],
            body=row["body"],
            moderator_user_id=row["moderator_user_id"],
            created_at=self._parse_datetime(row["created_at"]),
            applied_at=self._parse_optional_datetime(row["applied_at"]),
        )

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(UTC)

    @classmethod
    def _parse_optional_datetime(cls, value: str | None) -> datetime | None:
        if value is None:
            return None
        return cls._parse_datetime(value)


class ArticleDocumentParser:
    _FRONT_MATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
    _COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
    _IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    _LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    _HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
    _BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1", re.DOTALL)
    _ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)|_(?!\s)(.*?)(?<!\s)_", re.DOTALL)
    _INLINE_CODE_RE = re.compile(r"`([^`]+)`")
    _MULTI_BLANK_RE = re.compile(r"\n{3,}")

    def __init__(self, *, root_path: Path) -> None:
        self._root_path = root_path.expanduser()
        if not self._root_path.is_absolute():
            self._root_path = self._root_path.resolve()

    def scan(self) -> list[ArticleSource]:
        if not self._root_path.exists():
            return []
        sources: list[ArticleSource] = []
        for path in sorted(self._root_path.rglob("articles/*.md")):
            if not path.is_file():
                continue
            sources.append(self.load(path))
        return sources

    def load(self, path: Path) -> ArticleSource:
        resolved_path = path.expanduser()
        if not resolved_path.is_absolute():
            resolved_path = resolved_path.resolve()
        markdown_text = resolved_path.read_text(encoding="utf-8")
        relative_path = resolved_path.relative_to(self._root_path)
        publication = relative_path.parts[0] if len(relative_path.parts) > 1 else "default"
        slug = resolved_path.stem
        article_id = relative_path.with_suffix("").as_posix().replace("/", ":")
        title = self._extract_title(markdown_text, fallback=slug.replace("-", " ").strip())
        rendered_text = self._render_markdown(markdown_text, title=title)
        content_hash = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
        return ArticleSource(
            id=article_id,
            path=resolved_path,
            publication=publication,
            slug=slug,
            title=title,
            markdown_text=markdown_text,
            rendered_text=rendered_text,
            content_hash=content_hash,
        )

    def build_publish_request(self, source: ArticleSource) -> PublishRequest:
        return PublishRequest(
            text=source.rendered_text,
            parse_mode=None,
            disable_web_page_preview=True,
        )

    def apply_comment_instructions(self, path: Path, comment: str) -> bool:
        resolved_path = path.expanduser()
        if not resolved_path.is_absolute():
            resolved_path = resolved_path.resolve()
        content = resolved_path.read_text(encoding="utf-8")
        updated = content
        changed = False

        for raw_line in comment.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            if not value:
                continue
            directive = key.strip().lower()
            payload = self._decode_instruction_value(value.strip())
            if directive == "title":
                next_content = self._apply_title(updated, payload)
            elif directive == "replace":
                old_value, separator, new_value = payload.partition("=>")
                if not separator:
                    continue
                next_content = updated.replace(
                    old_value.strip(),
                    new_value.strip(),
                    1,
                )
            elif directive == "delete":
                next_content = updated.replace(payload, "")
            elif directive == "append":
                suffix = payload if updated.endswith("\n") else f"\n\n{payload}"
                next_content = f"{updated}{suffix}"
            elif directive == "prepend":
                next_content = f"{payload}\n\n{updated}"
            else:
                continue
            if next_content != updated:
                updated = next_content
                changed = True

        if not changed:
            return False
        resolved_path.write_text(updated, encoding="utf-8")
        return True

    def _extract_title(self, markdown_text: str, *, fallback: str) -> str:
        for raw_line in markdown_text.splitlines():
            line = raw_line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return fallback

    def _render_markdown(self, markdown_text: str, *, title: str) -> str:
        text = self._FRONT_MATTER_RE.sub("", markdown_text, count=1)
        text = self._COMMENT_RE.sub("", text)
        text = self._IMAGE_RE.sub(lambda match: match.group(1).strip(), text)
        text = self._LINK_RE.sub(
            lambda match: f"{match.group(1).strip()} ({match.group(2).strip()})",
            text,
        )
        text = self._HEADING_RE.sub("", text)
        text = self._BOLD_RE.sub(lambda match: match.group(2), text)
        text = self._ITALIC_RE.sub(
            lambda match: match.group(1) or match.group(2) or "",
            text,
        )
        text = self._INLINE_CODE_RE.sub(lambda match: match.group(1), text)
        text = text.replace("\r\n", "\n").strip()
        if not text.startswith(title):
            text = f"{title}\n\n{text}" if text else title
        text = self._MULTI_BLANK_RE.sub("\n\n", text)
        return text.strip()

    @staticmethod
    def _apply_title(markdown_text: str, title: str) -> str:
        lines = markdown_text.splitlines()
        for index, line in enumerate(lines):
            if line.startswith("# "):
                lines[index] = f"# {title}"
                return "\n".join(lines)
        return f"# {title}\n\n{markdown_text}".strip()

    @staticmethod
    def _decode_instruction_value(value: str) -> str:
        return value.replace("\\n", "\n").strip()


class ArticleLifecycleService:
    _ACTIVE_STATUSES = {
        "pending_review",
        "awaiting_schedule",
        "awaiting_rejection_comment",
        "scheduled",
        "published",
    }

    def __init__(
        self,
        *,
        settings: Settings,
        store: ArticleStore,
        submit_article_draft: Callable[[str, PublishRequest, str, int], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._store = store
        self._submit_article_draft = submit_article_draft
        self._parser = ArticleDocumentParser(root_path=settings.articles_root_path)

    async def sync_on_startup(self) -> None:
        if not self._settings.articles_auto_sync_on_startup:
            return
        sources = self._parser.scan()
        source_by_id = {source.id: source for source in sources}

        for source in sources:
            self._store.upsert_source(source)

        for article_id, source in source_by_id.items():
            record = self._store.get_record(article_id)
            try:
                await self._sync_article(record, source)
            except Exception as error:
                self._store.sync_status(
                    article_id,
                    status="failed",
                    last_error=str(error),
                )

    async def _sync_article(self, record: ArticleRecord, source: ArticleSource) -> None:
        if record.status in self._ACTIVE_STATUSES:
            return

        if record.status == "rejected":
            comment = self._store.latest_comment(article_id=record.id, unapplied_only=True)
            if record.last_submitted_hash != record.current_source_hash:
                await self._submit_source(record, source, comment=comment)
                return
            if comment is not None and self._parser.apply_comment_instructions(source.path, comment.body):
                refreshed_source = self._parser.load(source.path)
                refreshed_record = self._store.upsert_source(refreshed_source)
                await self._submit_source(refreshed_record, refreshed_source, comment=comment)
            return

        if record.status in {"draft", "failed"}:
            should_submit = record.last_submitted_hash is None or record.status == "failed"
            if should_submit:
                await self._submit_source(record, source)

    async def _submit_source(
        self,
        record: ArticleRecord,
        source: ArticleSource,
        *,
        comment: ArticleCommentRecord | None = None,
    ) -> None:
        attempt = self._store.count_draft_attempts(record.id) + 1
        request = self._parser.build_publish_request(source)
        await self._submit_article_draft(
            record.id,
            request,
            source.content_hash,
            attempt,
        )
        if comment is not None:
            self._store.mark_comment_applied(comment.id)
