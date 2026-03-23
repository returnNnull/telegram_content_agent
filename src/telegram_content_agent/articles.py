from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from telegram_content_agent.models import (
    ArticleCommentResponse,
    ArticleResponse,
    ArticleSnapshotRequest,
    PublishRequest,
)


class ArticleNotFoundError(RuntimeError):
    """Raised when an article cannot be found."""


@dataclass(slots=True)
class ArticleRecord:
    article_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    scheduled_publish_at: datetime | None
    published_at: datetime | None
    moderation_comment: str | None
    title: str
    slug: str
    cover_path: str | None
    payload_path: str | None
    source_refs: list[str]
    attached_links: list[str]
    last_synced_at: datetime | None
    publish_strategy: str | None
    last_error: str | None
    markdown: str
    payload: PublishRequest
    current_draft_id: str | None

    def to_response(self) -> ArticleResponse:
        return ArticleResponse(
            article_id=self.article_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            scheduled_publish_at=self.scheduled_publish_at,
            published_at=self.published_at,
            moderation_comment=self.moderation_comment,
            title=self.title,
            slug=self.slug,
            cover_path=self.cover_path,
            payload_path=self.payload_path,
            source_refs=self.source_refs,
            attached_links=self.attached_links,
            last_synced_at=self.last_synced_at,
            publish_strategy=self.publish_strategy,
            last_error=self.last_error,
            markdown=self.markdown,
            payload=self.payload,
            current_draft_id=self.current_draft_id,
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
                CREATE TABLE IF NOT EXISTS article_records (
                    article_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    scheduled_publish_at TEXT,
                    published_at TEXT,
                    moderation_comment TEXT,
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    cover_path TEXT,
                    payload_path TEXT,
                    source_refs_json TEXT NOT NULL,
                    attached_links_json TEXT NOT NULL,
                    last_synced_at TEXT,
                    publish_strategy TEXT,
                    last_error TEXT,
                    markdown TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    current_draft_id TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_article_records_status_updated
                ON article_records(status, updated_at DESC)
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
            self._backfill_legacy_articles(connection)
            connection.commit()

    def upsert_snapshot(
        self,
        snapshot: ArticleSnapshotRequest,
        *,
        status: str | None = None,
        publish_strategy: str | None = None,
        last_error: str | None = None,
    ) -> ArticleRecord:
        now = datetime.now(UTC)
        article_id = snapshot.article_id or uuid4().hex
        existing = self._get_optional(connection=None, article_id=article_id)
        created_at = snapshot.created_at or (existing.created_at if existing else now)
        updated_at = snapshot.updated_at or now
        record_status = self._normalize_article_status(
            status or (existing.status if existing else "draft")
        )
        request = self._sanitize_payload(snapshot.payload)
        row = {
            "article_id": article_id,
            "status": record_status,
            "created_at": self._iso(created_at),
            "updated_at": self._iso(updated_at),
            "scheduled_publish_at": self._iso(snapshot.scheduled_publish_at)
            if snapshot.scheduled_publish_at is not None
            else (self._iso(existing.scheduled_publish_at) if existing and existing.scheduled_publish_at else None),
            "published_at": self._iso(existing.published_at) if existing and existing.published_at else None,
            "moderation_comment": snapshot.moderation_comment
            if snapshot.moderation_comment is not None
            else (existing.moderation_comment if existing else None),
            "title": snapshot.title,
            "slug": snapshot.slug,
            "cover_path": snapshot.cover_path,
            "payload_path": snapshot.payload_path,
            "source_refs_json": json.dumps(snapshot.source_refs, ensure_ascii=False),
            "attached_links_json": json.dumps(snapshot.attached_links, ensure_ascii=False),
            "last_synced_at": self._iso(snapshot.last_synced_at)
            if snapshot.last_synced_at is not None
            else self._iso(now),
            "publish_strategy": publish_strategy
            or snapshot.publish_strategy
            or (existing.publish_strategy if existing else None),
            "last_error": last_error,
            "markdown": snapshot.markdown,
            "payload_json": request.model_dump_json(),
            "current_draft_id": existing.current_draft_id if existing else None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO article_records (
                    article_id, status, created_at, updated_at, scheduled_publish_at,
                    published_at, moderation_comment, title, slug, cover_path, payload_path,
                    source_refs_json, attached_links_json, last_synced_at, publish_strategy,
                    last_error, markdown, payload_json, current_draft_id
                ) VALUES (
                    :article_id, :status, :created_at, :updated_at, :scheduled_publish_at,
                    :published_at, :moderation_comment, :title, :slug, :cover_path, :payload_path,
                    :source_refs_json, :attached_links_json, :last_synced_at, :publish_strategy,
                    :last_error, :markdown, :payload_json, :current_draft_id
                )
                ON CONFLICT(article_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    scheduled_publish_at = excluded.scheduled_publish_at,
                    moderation_comment = excluded.moderation_comment,
                    title = excluded.title,
                    slug = excluded.slug,
                    cover_path = excluded.cover_path,
                    payload_path = excluded.payload_path,
                    source_refs_json = excluded.source_refs_json,
                    attached_links_json = excluded.attached_links_json,
                    last_synced_at = excluded.last_synced_at,
                    publish_strategy = excluded.publish_strategy,
                    last_error = excluded.last_error,
                    markdown = excluded.markdown,
                    payload_json = excluded.payload_json
                """,
                row,
            )
            connection.commit()
        return self.get_record(article_id)

    def attach_draft(self, article_id: str, *, draft_id: str, status: str) -> ArticleRecord:
        return self._update_article(
            article_id,
            """
            UPDATE article_records
            SET current_draft_id = ?,
                status = ?,
                updated_at = ?,
                last_error = NULL
            WHERE article_id = ?
            """,
            (
                draft_id,
                self._normalize_article_status(status),
                self._iso(datetime.now(UTC)),
                article_id,
            ),
        )

    def sync_state(
        self,
        article_id: str,
        *,
        status: str,
        scheduled_publish_at: datetime | None = None,
        published_at: datetime | None = None,
        moderation_comment: str | None = None,
        last_error: str | None = None,
        publish_strategy: str | None = None,
    ) -> ArticleRecord:
        existing = self.get_record(article_id)
        normalized_status = self._normalize_article_status(status)
        return self._update_article(
            article_id,
            """
            UPDATE article_records
            SET status = ?,
                updated_at = ?,
                scheduled_publish_at = ?,
                published_at = ?,
                moderation_comment = ?,
                last_error = ?,
                publish_strategy = ?,
                current_draft_id = CASE WHEN ? = 'draft' THEN NULL ELSE current_draft_id END
            WHERE article_id = ?
            """,
            (
                normalized_status,
                self._iso(datetime.now(UTC)),
                self._iso(scheduled_publish_at) if scheduled_publish_at is not None else None,
                self._iso(published_at) if published_at is not None else existing.published_at and self._iso(existing.published_at),
                moderation_comment if moderation_comment is not None else existing.moderation_comment,
                last_error,
                publish_strategy if publish_strategy is not None else existing.publish_strategy,
                normalized_status,
                article_id,
            ),
        )

    def list(self, *, status: str | None = None) -> list[ArticleResponse]:
        query = "SELECT * FROM article_records"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (self._normalize_article_status(status),)
        query += " ORDER BY updated_at DESC, created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_article(row).to_response() for row in rows]

    def get(self, article_id: str) -> ArticleResponse:
        return self.get_record(article_id).to_response()

    def get_record(self, article_id: str) -> ArticleRecord:
        record = self._get_optional(connection=None, article_id=article_id)
        if record is None:
            raise ArticleNotFoundError(f"Article not found: {article_id}")
        return record

    def get_publish_request(self, article_id: str) -> PublishRequest:
        return self.get_record(article_id).payload

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
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE article_comments
                SET applied_at = ?
                WHERE id = ?
                """,
                (self._iso(datetime.now(UTC)), comment_id),
            )
            if updated.rowcount == 0:
                raise ArticleNotFoundError(f"Comment not found: {comment_id}")
            connection.commit()
        return self.get_comment(comment_id)

    def count_draft_attempts(self, article_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM moderation_drafts WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        if row is None:
            return 0
        return int(row["total"])

    def _backfill_legacy_articles(self, connection: sqlite3.Connection) -> None:
        record_count = connection.execute(
            "SELECT COUNT(*) AS total FROM article_records"
        ).fetchone()["total"]
        if record_count:
            return

        legacy_articles_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'articles'
            """
        ).fetchone()
        if not legacy_articles_exists:
            return

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(articles)").fetchall()
        }
        if "source_path" not in columns:
            return

        legacy_rows = connection.execute("SELECT * FROM articles ORDER BY created_at ASC").fetchall()
        for row in legacy_rows:
            draft_payload = self._load_latest_legacy_payload(connection, row["id"])
            payload = (
                PublishRequest.model_validate_json(draft_payload)
                if draft_payload
                else PublishRequest(text=row["title"], dry_run=False)
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO article_records (
                    article_id, status, created_at, updated_at, scheduled_publish_at,
                    published_at, moderation_comment, title, slug, cover_path, payload_path,
                    source_refs_json, attached_links_json, last_synced_at, publish_strategy,
                    last_error, markdown, payload_json, current_draft_id
                ) VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, NULL, NULL, '[]', '[]', NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    self._normalize_article_status(row["status"]),
                    row["created_at"],
                    row["updated_at"],
                    row["published_at"],
                    row["title"],
                    row["slug"],
                    row["last_error"],
                    "",
                    payload.model_dump_json(),
                    row["current_draft_id"],
                ),
            )

    @staticmethod
    def _load_latest_legacy_payload(connection: sqlite3.Connection, article_id: str) -> str | None:
        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'moderation_drafts'
            """
        ).fetchone()
        if not table_exists:
            return None
        row = connection.execute(
            """
            SELECT request_json
            FROM moderation_drafts
            WHERE article_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (article_id,),
        ).fetchone()
        if row is None:
            return None
        return row["request_json"]

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

    def _get_optional(
        self,
        *,
        connection: sqlite3.Connection | None,
        article_id: str,
    ) -> ArticleRecord | None:
        if connection is None:
            with self._connect() as local_connection:
                row = local_connection.execute(
                    "SELECT * FROM article_records WHERE article_id = ?",
                    (article_id,),
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM article_records WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_article(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_article(self, row: sqlite3.Row) -> ArticleRecord:
        return ArticleRecord(
            article_id=row["article_id"],
            status=self._normalize_article_status(row["status"]),
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
            scheduled_publish_at=self._parse_optional_datetime(row["scheduled_publish_at"]),
            published_at=self._parse_optional_datetime(row["published_at"]),
            moderation_comment=row["moderation_comment"],
            title=row["title"],
            slug=row["slug"],
            cover_path=row["cover_path"],
            payload_path=row["payload_path"],
            source_refs=list(json.loads(row["source_refs_json"] or "[]")),
            attached_links=list(json.loads(row["attached_links_json"] or "[]")),
            last_synced_at=self._parse_optional_datetime(row["last_synced_at"]),
            publish_strategy=row["publish_strategy"],
            last_error=row["last_error"],
            markdown=row["markdown"],
            payload=PublishRequest.model_validate_json(row["payload_json"]),
            current_draft_id=row["current_draft_id"],
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
    def _sanitize_payload(payload: PublishRequest) -> PublishRequest:
        return payload.model_copy(update={"dry_run": False, "chat_id": None})

    @staticmethod
    def _normalize_article_status(status: str) -> str:
        if status == "awaiting_rejection_comment":
            return "pending_review"
        return status

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


@dataclass(slots=True)
class LocalArticleDocument:
    article_id: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    scheduled_publish_at: datetime | None
    moderation_comment: str | None
    title: str
    slug: str
    cover_path: str | None
    payload_path: str | None
    source_refs: list[str]
    attached_links: list[str]
    last_synced_at: datetime | None
    publish_strategy: str | None
    last_error: str | None
    body: str
    path: Path | None = None


@dataclass(slots=True)
class LocalArticleCard:
    article_id: str | None
    slug: str
    status: str
    path: str
    updated_at: str | None


class LocalArticleRepository:
    def __init__(self, publication_root: Path) -> None:
        self._publication_root = publication_root.expanduser()
        if not self._publication_root.is_absolute():
            self._publication_root = self._publication_root.resolve()
        self._workspace_root = self._publication_root.parent.parent
        self._articles_root = self._publication_root / "articles"
        self._active_dir = self._articles_root / "active"
        self._archive_dir = self._articles_root / "archive"
        self._index_path = self._articles_root / "index.json"

    @property
    def active_dir(self) -> Path:
        return self._active_dir

    @property
    def archive_dir(self) -> Path:
        return self._archive_dir

    @property
    def index_path(self) -> Path:
        return self._index_path

    def initialize(self) -> None:
        self._active_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._write_index({"active": [], "archive": []})

    def read_index(self) -> dict[str, list[dict[str, Any]]]:
        self.initialize()
        raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        return {
            "active": list(raw.get("active", [])),
            "archive": list(raw.get("archive", [])),
        }

    def load_article(self, path: Path) -> LocalArticleDocument:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            raise ValueError(f"Markdown article does not have YAML front matter: {path}")
        _, _, tail = content.partition("---\n")
        front_matter, _, body = tail.partition("\n---\n")
        metadata = self._parse_front_matter(front_matter)
        return LocalArticleDocument(
            article_id=metadata.get("article_id"),
            status=metadata["status"],
            created_at=self._parse_optional_front_matter_datetime(metadata.get("created_at")),
            updated_at=self._parse_optional_front_matter_datetime(metadata.get("updated_at")),
            scheduled_publish_at=self._parse_optional_front_matter_datetime(
                metadata.get("scheduled_publish_at")
            ),
            moderation_comment=metadata.get("moderation_comment"),
            title=metadata["title"],
            slug=metadata["slug"],
            cover_path=metadata.get("cover_path"),
            payload_path=metadata.get("payload_path"),
            source_refs=list(metadata.get("source_refs", [])),
            attached_links=list(metadata.get("attached_links", [])),
            last_synced_at=self._parse_optional_front_matter_datetime(
                metadata.get("last_synced_at")
            ),
            publish_strategy=metadata.get("publish_strategy"),
            last_error=metadata.get("last_error"),
            body=body,
            path=path,
        )

    def save_article(self, article: LocalArticleDocument, *, archive: bool = False) -> Path:
        self.initialize()
        target_dir = self._archive_dir if archive else self._active_dir
        target_path = target_dir / f"{article.slug}.md"
        target_path.write_text(self._render_article(article), encoding="utf-8")
        self._update_index(article, target_path, archive=archive)
        return target_path

    def list_active_cards(self) -> list[LocalArticleCard]:
        index = self.read_index()
        return [self._card_from_dict(item) for item in index["active"]]

    def archive_article(self, *, article_id: str | None = None, slug: str | None = None) -> Path:
        if article_id is None and slug is None:
            raise ValueError("archive_article requires article_id or slug.")
        index = self.read_index()
        match = None
        for item in index["active"]:
            if item.get("article_id") == article_id or item.get("slug") == slug:
                match = item
                break
        if match is None:
            raise FileNotFoundError("Active article not found in index.json.")
        source_path = self._workspace_root / match["path"]
        article = self.load_article(source_path)
        target_path = self._archive_dir / source_path.name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target_path)
        article.path = target_path
        self._update_index(article, target_path, archive=True)
        return target_path

    def _update_index(self, article: LocalArticleDocument, path: Path, *, archive: bool) -> None:
        index = self.read_index()
        relative_path = path.relative_to(self._workspace_root).as_posix()
        card = {
            "article_id": article.article_id,
            "slug": article.slug,
            "status": article.status,
            "path": relative_path,
            "updated_at": article.updated_at.astimezone(UTC).isoformat()
            if article.updated_at is not None
            else None,
        }
        active = [
            item
            for item in index["active"]
            if item.get("path") != relative_path and item.get("slug") != article.slug
        ]
        archive_items = [
            item
            for item in index["archive"]
            if item.get("path") != relative_path and item.get("slug") != article.slug
        ]
        if archive:
            archive_items.insert(0, card)
        else:
            active.insert(0, card)
        self._write_index({"active": active, "archive": archive_items})

    def _write_index(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        self._index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _render_article(self, article: LocalArticleDocument) -> str:
        metadata = {
            "article_id": article.article_id,
            "status": article.status,
            "created_at": self._format_front_matter_datetime(article.created_at),
            "updated_at": self._format_front_matter_datetime(article.updated_at),
            "scheduled_publish_at": self._format_front_matter_datetime(article.scheduled_publish_at),
            "moderation_comment": article.moderation_comment,
            "title": article.title,
            "slug": article.slug,
            "cover_path": article.cover_path,
            "payload_path": article.payload_path,
            "source_refs": article.source_refs,
            "attached_links": article.attached_links,
            "last_synced_at": self._format_front_matter_datetime(article.last_synced_at),
            "publish_strategy": article.publish_strategy,
            "last_error": article.last_error,
        }
        front_matter = self._dump_front_matter(metadata)
        body = article.body.lstrip("\n")
        if not body.endswith("\n"):
            body += "\n"
        return f"---\n{front_matter}\n---\n{body}"

    @classmethod
    def _parse_front_matter(cls, front_matter: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        current_list_key: str | None = None
        for raw_line in front_matter.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            if line.startswith("  - "):
                if current_list_key is None:
                    raise ValueError("Invalid YAML front matter list item.")
                result.setdefault(current_list_key, []).append(cls._parse_scalar(line[4:]))
                continue
            current_list_key = None
            key, separator, raw_value = line.partition(":")
            if not separator:
                raise ValueError(f"Invalid YAML front matter line: {line}")
            value = raw_value.strip()
            if value == "":
                result[key] = []
                current_list_key = key
                continue
            result[key] = cls._parse_scalar(value)
        return result

    @staticmethod
    def _dump_front_matter(metadata: dict[str, Any]) -> str:
        lines: list[str] = []
        for key, value in metadata.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {json.dumps(item, ensure_ascii=False)}")
                continue
            lines.append(f"{key}: {LocalArticleRepository._dump_scalar(value)}")
        return "\n".join(lines)

    @staticmethod
    def _dump_scalar(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _parse_scalar(value: str) -> Any:
        if value == "null":
            return None
        if value == "true":
            return True
        if value == "false":
            return False
        if value.startswith('"') or value.startswith("[") or value.startswith("{"):
            return json.loads(value)
        return value

    @staticmethod
    def _format_front_matter_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _parse_optional_front_matter_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return datetime.fromisoformat(str(value)).astimezone(UTC)

    @staticmethod
    def _card_from_dict(item: dict[str, Any]) -> LocalArticleCard:
        return LocalArticleCard(
            article_id=item.get("article_id"),
            slug=item["slug"],
            status=item["status"],
            path=item["path"],
            updated_at=item.get("updated_at"),
        )
