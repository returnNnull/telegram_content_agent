from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from telegram_content_agent.articles import ArticleStore
from telegram_content_agent.config import Settings
from telegram_content_agent.models import (
    ModerationDraftResponse,
    PublishRequest,
    ScheduledPostResponse,
    SubmitDraftRequest,
)
from telegram_content_agent.telegram_client import TelegramPublishError, TelegramPublisher

if TYPE_CHECKING:
    from telegram_content_agent.scheduler import ScheduledPublisher


class ModerationDraftNotFoundError(RuntimeError):
    """Raised when a moderation draft cannot be found."""


class ModerationDraftConflictError(RuntimeError):
    """Raised when a moderation draft cannot transition to the requested state."""


@dataclass(slots=True)
class ModerationDraftRecord:
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    rejected_at: datetime | None
    scheduled_publish_at: datetime | None
    scheduled_post_id: str | None
    last_error: str | None
    request: PublishRequest
    publication_result: dict[str, Any] | None
    review_chat_id: str | None
    review_control_message_id: int | None
    article_id: str | None
    article_attempt: int | None
    article_source_hash: str | None

    def to_response(self) -> ModerationDraftResponse:
        return ModerationDraftResponse(
            id=self.id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            published_at=self.published_at,
            rejected_at=self.rejected_at,
            scheduled_publish_at=self.scheduled_publish_at,
            scheduled_post_id=self.scheduled_post_id,
            last_error=self.last_error,
            request=self.request,
            publication_result=self.publication_result,
            article_id=self.article_id,
            article_attempt=self.article_attempt,
            article_source_hash=self.article_source_hash,
        )


class ModerationStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        if not self._db_path.is_absolute():
            self._db_path = self._db_path.resolve()

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_drafts (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    rejected_at TEXT,
                    scheduled_publish_at TEXT,
                    scheduled_post_id TEXT,
                    last_error TEXT,
                    request_json TEXT NOT NULL,
                    publication_result_json TEXT,
                    review_chat_id TEXT,
                    review_control_message_id INTEGER,
                    article_id TEXT,
                    article_attempt INTEGER,
                    article_source_hash TEXT
                )
                """
            )
            self._ensure_column(connection, "moderation_drafts", "article_id", "TEXT")
            self._ensure_column(connection, "moderation_drafts", "article_attempt", "INTEGER")
            self._ensure_column(connection, "moderation_drafts", "article_source_hash", "TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_schedule_inputs (
                    chat_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    draft_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_rejection_inputs (
                    chat_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    draft_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_moderation_drafts_scheduled_post_id
                ON moderation_drafts(scheduled_post_id)
                WHERE scheduled_post_id IS NOT NULL
                """
            )
            connection.commit()

    def create_draft(
        self,
        request: PublishRequest,
        *,
        article_id: str | None = None,
        article_attempt: int | None = None,
        article_source_hash: str | None = None,
    ) -> ModerationDraftRecord:
        now = datetime.now(UTC)
        draft_id = uuid4().hex
        row = {
            "id": draft_id,
            "status": "pending_review",
            "created_at": self._iso(now),
            "updated_at": self._iso(now),
            "published_at": None,
            "rejected_at": None,
            "scheduled_publish_at": None,
            "scheduled_post_id": None,
            "last_error": None,
            "request_json": request.model_dump_json(),
            "publication_result_json": None,
            "review_chat_id": None,
            "review_control_message_id": None,
            "article_id": article_id,
            "article_attempt": article_attempt,
            "article_source_hash": article_source_hash,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO moderation_drafts (
                    id, status, created_at, updated_at, published_at, rejected_at,
                    scheduled_publish_at, scheduled_post_id, last_error, request_json,
                    publication_result_json, review_chat_id, review_control_message_id,
                    article_id, article_attempt, article_source_hash
                ) VALUES (
                    :id, :status, :created_at, :updated_at, :published_at, :rejected_at,
                    :scheduled_publish_at, :scheduled_post_id, :last_error, :request_json,
                    :publication_result_json, :review_chat_id, :review_control_message_id,
                    :article_id, :article_attempt, :article_source_hash
                )
                """,
                row,
            )
            connection.commit()
        return self.get_record(draft_id)

    def list(self, *, status: str | None = None) -> list[ModerationDraftResponse]:
        query = "SELECT * FROM moderation_drafts"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row).to_response() for row in rows]

    def get(self, draft_id: str) -> ModerationDraftResponse:
        return self.get_record(draft_id).to_response()

    def get_record(self, draft_id: str) -> ModerationDraftRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM moderation_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise ModerationDraftNotFoundError(f"Moderation draft not found: {draft_id}")
        return self._row_to_record(row)

    def find_by_scheduled_post_id(self, scheduled_post_id: str) -> ModerationDraftRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM moderation_drafts WHERE scheduled_post_id = ?",
                (scheduled_post_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def attach_review_message(
        self,
        draft_id: str,
        *,
        chat_id: str,
        message_id: int,
    ) -> ModerationDraftRecord:
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET review_chat_id = ?,
                review_control_message_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (chat_id, message_id, self._iso(datetime.now(UTC)), draft_id),
        )

    def mark_awaiting_schedule(self, draft_id: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "awaiting_schedule":
            return current
        if current.status != "pending_review":
            raise ModerationDraftConflictError(
                "Only pending_review drafts can wait for schedule input."
            )
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'awaiting_schedule',
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), draft_id),
        )

    def mark_awaiting_rejection_comment(self, draft_id: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "awaiting_rejection_comment":
            return current
        if current.status not in {"pending_review", "awaiting_schedule"}:
            raise ModerationDraftConflictError(
                "Only pending_review or awaiting_schedule drafts can wait for rejection comment."
            )
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'awaiting_rejection_comment',
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), draft_id),
        )

    def reset_to_pending_review(self, draft_id: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "pending_review":
            return current
        if current.status != "awaiting_schedule":
            raise ModerationDraftConflictError(
                "Only drafts waiting for schedule input can return to pending_review."
            )
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'pending_review',
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), draft_id),
        )

    def reset_rejection_request(self, draft_id: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "pending_review":
            return current
        if current.status != "awaiting_rejection_comment":
            raise ModerationDraftConflictError(
                "Only drafts waiting for rejection comment can return to pending_review."
            )
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'pending_review',
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), draft_id),
        )

    def mark_rejected(self, draft_id: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "rejected":
            return current
        if current.status not in {
            "pending_review",
            "awaiting_schedule",
            "awaiting_rejection_comment",
        }:
            raise ModerationDraftConflictError(
                "Only pending_review, awaiting_schedule or awaiting_rejection_comment drafts can be rejected."
            )
        now = datetime.now(UTC)
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'rejected',
                updated_at = ?,
                rejected_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (self._iso(now), self._iso(now), draft_id),
        )

    def mark_scheduled(
        self,
        draft_id: str,
        *,
        scheduled_post_id: str,
        publish_at: datetime,
    ) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status not in {"pending_review", "awaiting_schedule"}:
            raise ModerationDraftConflictError(
                "Only pending_review or awaiting_schedule drafts can be scheduled."
            )
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'scheduled',
                updated_at = ?,
                scheduled_post_id = ?,
                scheduled_publish_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (
                self._iso(datetime.now(UTC)),
                scheduled_post_id,
                self._iso(publish_at),
                draft_id,
            ),
        )

    def mark_published(
        self,
        draft_id: str,
        *,
        result: dict[str, Any] | None,
    ) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "published":
            return current
        if current.status not in {"pending_review", "awaiting_schedule", "scheduled"}:
            raise ModerationDraftConflictError("Draft cannot be marked as published.")
        now = datetime.now(UTC)
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'published',
                updated_at = ?,
                published_at = ?,
                last_error = NULL,
                publication_result_json = ?
            WHERE id = ?
            """,
            (
                self._iso(now),
                self._iso(now),
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                draft_id,
            ),
        )

    def mark_failed(self, draft_id: str, *, error_message: str) -> ModerationDraftRecord:
        current = self.get_record(draft_id)
        if current.status == "failed":
            return current
        if current.status not in {
            "pending_review",
            "awaiting_schedule",
            "awaiting_rejection_comment",
            "scheduled",
        }:
            raise ModerationDraftConflictError("Draft cannot be marked as failed.")
        return self._update_record(
            draft_id,
            """
            UPDATE moderation_drafts
            SET status = 'failed',
                updated_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (self._iso(datetime.now(UTC)), error_message, draft_id),
        )

    def set_pending_schedule(self, *, chat_id: str, user_id: int, draft_id: str) -> None:
        created_at = self._iso(datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_schedule_inputs WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            connection.execute(
                """
                INSERT INTO pending_schedule_inputs (chat_id, user_id, draft_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, user_id, draft_id, created_at),
            )
            connection.commit()

    def get_pending_schedule(self, *, chat_id: str, user_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT draft_id
                FROM pending_schedule_inputs
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return row["draft_id"]

    def clear_pending_schedule(self, *, chat_id: str, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_schedule_inputs WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            connection.commit()

    def clear_pending_schedule_for_draft(self, draft_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_schedule_inputs WHERE draft_id = ?",
                (draft_id,),
            )
            connection.commit()

    def set_pending_rejection(self, *, chat_id: str, user_id: int, draft_id: str) -> None:
        created_at = self._iso(datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_rejection_inputs WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            connection.execute(
                """
                INSERT INTO pending_rejection_inputs (chat_id, user_id, draft_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, user_id, draft_id, created_at),
            )
            connection.commit()

    def get_pending_rejection(self, *, chat_id: str, user_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT draft_id
                FROM pending_rejection_inputs
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return row["draft_id"]

    def clear_pending_rejection(self, *, chat_id: str, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_rejection_inputs WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            connection.commit()

    def clear_pending_rejection_for_draft(self, draft_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_rejection_inputs WHERE draft_id = ?",
                (draft_id,),
            )
            connection.commit()

    def _update_record(
        self,
        draft_id: str,
        statement: str,
        params: tuple[Any, ...],
    ) -> ModerationDraftRecord:
        with self._connect() as connection:
            updated = connection.execute(statement, params)
            if updated.rowcount == 0:
                raise ModerationDraftNotFoundError(f"Moderation draft not found: {draft_id}")
            connection.commit()
        return self.get_record(draft_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_record(self, row: sqlite3.Row) -> ModerationDraftRecord:
        publication_result = row["publication_result_json"]
        return ModerationDraftRecord(
            id=row["id"],
            status=row["status"],
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
            published_at=self._parse_optional_datetime(row["published_at"]),
            rejected_at=self._parse_optional_datetime(row["rejected_at"]),
            scheduled_publish_at=self._parse_optional_datetime(row["scheduled_publish_at"]),
            scheduled_post_id=row["scheduled_post_id"],
            last_error=row["last_error"],
            request=PublishRequest.model_validate_json(row["request_json"]),
            publication_result=json.loads(publication_result) if publication_result else None,
            review_chat_id=row["review_chat_id"],
            review_control_message_id=row["review_control_message_id"],
            article_id=row["article_id"],
            article_attempt=row["article_attempt"],
            article_source_hash=row["article_source_hash"],
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name in existing_columns:
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
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


class ModerationService:
    _STATUS_LABELS = {
        "pending_review": "ждет подтверждения",
        "awaiting_schedule": "ждет время публикации",
        "awaiting_rejection_comment": "ждет комментарий к отклонению",
        "scheduled": "запланирован",
        "published": "опубликован",
        "rejected": "отклонен",
        "failed": "с ошибкой",
    }

    def __init__(
        self,
        *,
        settings: Settings,
        store: ModerationStore,
        article_store: ArticleStore | None,
        moderation_publisher: TelegramPublisher,
        channel_publisher: TelegramPublisher,
        scheduler: ScheduledPublisher,
    ) -> None:
        self._settings = settings
        self._store = store
        self._article_store = article_store
        self._moderation_publisher = moderation_publisher
        self._channel_publisher = channel_publisher
        self._scheduler = scheduler
        self._timezone = ZoneInfo(settings.moderation_timezone)

    async def submit_draft(self, request: SubmitDraftRequest) -> ModerationDraftResponse:
        sanitized_request = PublishRequest.model_validate(
            request.model_dump(
                exclude={"dry_run", "chat_id"},
            )
        )
        draft = await self._submit_internal(sanitized_request)
        return draft.to_response()

    async def submit_article_draft(
        self,
        article_id: str,
        request: PublishRequest,
        source_hash: str,
        article_attempt: int,
    ) -> ModerationDraftResponse:
        draft = await self._submit_internal(
            request,
            article_id=article_id,
            article_source_hash=source_hash,
            article_attempt=article_attempt,
        )
        return draft.to_response()

    async def _submit_internal(
        self,
        request: PublishRequest,
        *,
        article_id: str | None = None,
        article_source_hash: str | None = None,
        article_attempt: int | None = None,
    ) -> ModerationDraftRecord:
        draft = self._store.create_draft(
            request,
            article_id=article_id,
            article_attempt=article_attempt,
            article_source_hash=article_source_hash,
        )
        if self._article_store is not None and article_id is not None and article_source_hash is not None:
            self._article_store.attach_draft(
                article_id,
                draft_id=draft.id,
                source_hash=article_source_hash,
                status=draft.status,
            )
        try:
            await self._moderation_publisher.publish(
                request.model_copy(
                    update={
                        "dry_run": False,
                        "chat_id": self._settings.moderation_chat_id,
                    }
                )
            )
            control_response = await self._moderation_publisher.send_message(
                chat_id=self._settings.moderation_chat_id,
                text=self._render_control_text(draft),
                parse_mode="HTML",
                reply_markup=self._keyboard_for_status(draft),
                disable_web_page_preview=True,
            )
            self._store.attach_review_message(
                draft.id,
                chat_id=self._settings.moderation_chat_id,
                message_id=control_response["result"]["message_id"],
            )
        except Exception as error:
            draft = self._store.mark_failed(
                draft.id,
                error_message=f"Failed to deliver draft to moderation chat: {error}",
            )
            self._sync_article_status(draft)
            raise
        draft = self._store.get_record(draft.id)
        self._sync_article_status(draft)
        return draft

    async def handle_update(self, update: dict[str, Any]) -> None:
        callback_query = update.get("callback_query")
        if callback_query:
            await self._handle_callback_query(callback_query)
            return
        message = update.get("message")
        if message:
            await self._handle_message(message)

    async def sync_scheduled_post(self, post: ScheduledPostResponse) -> None:
        draft = self._store.find_by_scheduled_post_id(post.id)
        if draft is None:
            return
        if post.status == "published":
            draft = self._store.mark_published(
                draft.id,
                result=post.last_result,
            )
        elif post.status == "failed":
            draft = self._store.mark_failed(
                draft.id,
                error_message=post.last_error or "Scheduled publish failed.",
            )
        else:
            return
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)

    async def _handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        data = callback_query.get("data") or ""
        if not data.startswith("draft:"):
            return
        message = callback_query.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id"))
        user = callback_query.get("from") or {}
        user_id = user.get("id")
        callback_query_id = callback_query.get("id")

        if chat_id != self._settings.moderation_chat_id or user_id is None:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="Это действие доступно только в moderation-чате.",
                    show_alert=True,
                )
            return
        if not self._is_allowed_user(user_id):
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="У вас нет прав на модерацию.",
                    show_alert=True,
                )
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="Некорректная команда.",
                    show_alert=True,
                )
            return
        _, action, draft_id = parts

        try:
            draft = self._store.get_record(draft_id)
        except ModerationDraftNotFoundError:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="Черновик не найден.",
                    show_alert=True,
                )
            return

        if action == "publish":
            await self._publish_now(draft, callback_query_id=callback_query_id)
            return
        if action == "schedule":
            await self._request_schedule(
                draft,
                chat_id=chat_id,
                user_id=user_id,
                reply_to_message_id=message.get("message_id"),
                callback_query_id=callback_query_id,
            )
            return
        if action == "reject":
            await self._request_rejection_comment(
                draft,
                chat_id=chat_id,
                user_id=user_id,
                reply_to_message_id=message.get("message_id"),
                callback_query_id=callback_query_id,
            )
            return
        if action == "reset":
            await self._reset_schedule_request(
                draft,
                chat_id=chat_id,
                user_id=user_id,
                callback_query_id=callback_query_id,
            )
            return
        if action == "cancel_reject":
            await self._reset_rejection_request(
                draft,
                chat_id=chat_id,
                user_id=user_id,
                callback_query_id=callback_query_id,
            )

    async def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id"))
        if chat_id != self._settings.moderation_chat_id:
            return

        user = message.get("from") or {}
        user_id = user.get("id")
        if user_id is None or not self._is_allowed_user(user_id):
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        schedule_draft_id = self._store.get_pending_schedule(chat_id=chat_id, user_id=user_id)
        rejection_draft_id = self._store.get_pending_rejection(chat_id=chat_id, user_id=user_id)
        if schedule_draft_id is None and rejection_draft_id is None:
            return

        if text.lower() == "/cancel":
            if schedule_draft_id is not None:
                try:
                    draft = self._store.reset_to_pending_review(schedule_draft_id)
                except (ModerationDraftConflictError, ModerationDraftNotFoundError):
                    self._store.clear_pending_schedule(chat_id=chat_id, user_id=user_id)
                    return
                self._store.clear_pending_schedule(chat_id=chat_id, user_id=user_id)
                self._sync_article_status(draft)
                await self._refresh_control_message(draft)
                await self._moderation_publisher.send_message(
                    chat_id=chat_id,
                    text="Ввод времени отменен. Черновик снова ждет решения.",
                    reply_to_message_id=message.get("message_id"),
                )
                return

            try:
                draft = self._store.reset_rejection_request(rejection_draft_id)
            except (ModerationDraftConflictError, ModerationDraftNotFoundError):
                self._store.clear_pending_rejection(chat_id=chat_id, user_id=user_id)
                return
            self._store.clear_pending_rejection(chat_id=chat_id, user_id=user_id)
            self._sync_article_status(draft)
            await self._refresh_control_message(draft)
            await self._moderation_publisher.send_message(
                chat_id=chat_id,
                text="Отклонение отменено. Черновик снова ждет решения.",
                reply_to_message_id=message.get("message_id"),
            )
            return

        if schedule_draft_id is not None:
            publish_at = self._parse_publish_time(text)
            if publish_at is None:
                await self._moderation_publisher.send_message(
                    chat_id=chat_id,
                    text=self._schedule_prompt(invalid_value=text),
                    reply_to_message_id=message.get("message_id"),
                    disable_web_page_preview=True,
                )
                return

            try:
                draft = self._store.get_record(schedule_draft_id)
                scheduled_post = self._scheduler.create(
                    request=draft.request.model_copy(update={"dry_run": False, "chat_id": None}),
                    publish_at=publish_at,
                )
                self._store.clear_pending_schedule(chat_id=chat_id, user_id=user_id)
                draft = self._store.mark_scheduled(
                    draft.id,
                    scheduled_post_id=scheduled_post.id,
                    publish_at=publish_at,
                )
            except (ModerationDraftConflictError, ModerationDraftNotFoundError):
                self._store.clear_pending_schedule(chat_id=chat_id, user_id=user_id)
                return

            self._sync_article_status(draft)
            await self._refresh_control_message(draft)
            await self._moderation_publisher.send_message(
                chat_id=chat_id,
                text=(
                    "Публикация запланирована на "
                    f"{self._format_datetime(publish_at)} ({self._settings.moderation_timezone})."
                ),
                reply_to_message_id=message.get("message_id"),
            )
            return

        try:
            draft = self._store.get_record(rejection_draft_id)
            if self._article_store is not None:
                self._article_store.create_comment(
                    draft_id=draft.id,
                    article_id=draft.article_id,
                    body=text,
                    moderator_user_id=user_id,
                )
            self._store.clear_pending_rejection(chat_id=chat_id, user_id=user_id)
            self._store.clear_pending_rejection_for_draft(draft.id)
            draft = self._store.mark_rejected(draft.id)
        except (ModerationDraftConflictError, ModerationDraftNotFoundError):
            self._store.clear_pending_rejection(chat_id=chat_id, user_id=user_id)
            return

        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        await self._moderation_publisher.send_message(
            chat_id=chat_id,
            text="Черновик отклонен. Комментарий сохранен.",
            reply_to_message_id=message.get("message_id"),
        )

    async def _publish_now(
        self,
        draft: ModerationDraftRecord,
        *,
        callback_query_id: str | None,
    ) -> None:
        if draft.status not in {"pending_review", "awaiting_schedule"}:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text=f"Черновик уже {self._STATUS_LABELS.get(draft.status, draft.status)}.",
                    show_alert=False,
                )
            return

        try:
            result = await self._channel_publisher.publish(
                draft.request.model_copy(update={"dry_run": False, "chat_id": None})
            )
        except Exception as error:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text=f"Не удалось опубликовать: {self._short_error(error)}",
                    show_alert=True,
                )
            return

        self._store.clear_pending_schedule_for_draft(draft.id)
        self._store.clear_pending_rejection_for_draft(draft.id)
        draft = self._store.mark_published(draft.id, result=result)
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        if callback_query_id:
            await self._safe_answer_callback_query(
                callback_query_id,
                text="Опубликовано в основной канал.",
                show_alert=False,
            )

    async def _request_schedule(
        self,
        draft: ModerationDraftRecord,
        *,
        chat_id: str,
        user_id: int,
        reply_to_message_id: int | None,
        callback_query_id: str | None,
    ) -> None:
        try:
            draft = self._store.mark_awaiting_schedule(draft.id)
        except ModerationDraftConflictError:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text=f"Черновик уже {self._STATUS_LABELS.get(draft.status, draft.status)}.",
                    show_alert=False,
                )
            return

        self._store.set_pending_schedule(chat_id=chat_id, user_id=user_id, draft_id=draft.id)
        self._store.clear_pending_rejection_for_draft(draft.id)
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        await self._moderation_publisher.send_message(
            chat_id=chat_id,
            text=self._schedule_prompt(),
            reply_to_message_id=reply_to_message_id,
            disable_web_page_preview=True,
        )
        if callback_query_id:
            await self._safe_answer_callback_query(
                callback_query_id,
                text="Пришлите время публикации сообщением.",
                show_alert=False,
            )

    async def _request_rejection_comment(
        self,
        draft: ModerationDraftRecord,
        *,
        chat_id: str,
        user_id: int,
        reply_to_message_id: int | None,
        callback_query_id: str | None,
    ) -> None:
        try:
            self._store.clear_pending_schedule_for_draft(draft.id)
            self._store.clear_pending_rejection_for_draft(draft.id)
            draft = self._store.mark_awaiting_rejection_comment(draft.id)
        except ModerationDraftConflictError:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text=f"Черновик уже {self._STATUS_LABELS.get(draft.status, draft.status)}.",
                    show_alert=False,
                )
            return
        self._store.set_pending_rejection(chat_id=chat_id, user_id=user_id, draft_id=draft.id)
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        await self._moderation_publisher.send_message(
            chat_id=chat_id,
            text=self._rejection_prompt(),
            reply_to_message_id=reply_to_message_id,
            disable_web_page_preview=True,
        )
        if callback_query_id:
            await self._safe_answer_callback_query(
                callback_query_id,
                text="Пришлите комментарий к отклонению сообщением.",
                show_alert=False,
            )

    async def _reset_schedule_request(
        self,
        draft: ModerationDraftRecord,
        *,
        chat_id: str,
        user_id: int,
        callback_query_id: str | None,
    ) -> None:
        try:
            draft = self._store.reset_to_pending_review(draft.id)
        except ModerationDraftConflictError:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="Для этого черновика нет активного ожидания времени.",
                    show_alert=False,
                )
            return
        self._store.clear_pending_schedule(chat_id=chat_id, user_id=user_id)
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        if callback_query_id:
            await self._safe_answer_callback_query(
                callback_query_id,
                text="Ожидание времени отменено.",
                show_alert=False,
            )

    async def _reset_rejection_request(
        self,
        draft: ModerationDraftRecord,
        *,
        chat_id: str,
        user_id: int,
        callback_query_id: str | None,
    ) -> None:
        try:
            draft = self._store.reset_rejection_request(draft.id)
        except ModerationDraftConflictError:
            if callback_query_id:
                await self._safe_answer_callback_query(
                    callback_query_id,
                    text="Для этого черновика нет активного ввода комментария.",
                    show_alert=False,
                )
            return
        self._store.clear_pending_rejection(chat_id=chat_id, user_id=user_id)
        self._sync_article_status(draft)
        await self._refresh_control_message(draft)
        if callback_query_id:
            await self._safe_answer_callback_query(
                callback_query_id,
                text="Отклонение отменено.",
                show_alert=False,
            )

    async def _refresh_control_message(self, draft: ModerationDraftRecord) -> None:
        if draft.review_chat_id is None or draft.review_control_message_id is None:
            return
        try:
            await self._moderation_publisher.edit_message_text(
                chat_id=draft.review_chat_id,
                message_id=draft.review_control_message_id,
                text=self._render_control_text(draft),
                parse_mode="HTML",
                reply_markup=self._keyboard_for_status(draft),
                disable_web_page_preview=True,
            )
        except TelegramPublishError:
            return

    async def _safe_answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str,
        show_alert: bool,
    ) -> None:
        try:
            await self._moderation_publisher.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text[:200],
                show_alert=show_alert,
            )
        except TelegramPublishError:
            return

    def _sync_article_status(self, draft: ModerationDraftRecord) -> None:
        if self._article_store is None or draft.article_id is None:
            return
        self._article_store.sync_status(
            draft.article_id,
            status=draft.status,
            published_at=draft.published_at if draft.status == "published" else None,
            last_error=draft.last_error,
        )

    def _render_control_text(self, draft: ModerationDraftRecord) -> str:
        lines = [
            f"<b>Черновик</b> <code>{draft.id[:8]}</code>",
            f"Статус: <b>{escape(self._STATUS_LABELS.get(draft.status, draft.status))}</b>",
            f"Канал: <code>{escape(self._settings.telegram_channel_id)}</code>",
        ]
        if draft.article_id is not None:
            lines.append(f"Статья: <code>{escape(draft.article_id)}</code>")
        if draft.status == "awaiting_schedule":
            lines.append(
                "Отправьте время отдельным сообщением. Поддерживаются форматы "
                "<code>YYYY-MM-DD HH:MM</code>, <code>DD.MM.YYYY HH:MM</code>, "
                "<code>HH:MM</code> и ISO 8601."
            )
        if draft.status == "awaiting_rejection_comment":
            lines.append(
                "Отправьте комментарий к отклонению отдельным сообщением. "
                "Чтобы выйти из режима отклонения, отправьте <code>/cancel</code>."
            )
        if draft.scheduled_publish_at is not None:
            lines.append(
                "Публикация: "
                f"<b>{escape(self._format_datetime(draft.scheduled_publish_at))}</b> "
                f"({escape(self._settings.moderation_timezone)})"
            )
        if draft.published_at is not None:
            lines.append(
                "Опубликован: "
                f"<b>{escape(self._format_datetime(draft.published_at))}</b> "
                f"({escape(self._settings.moderation_timezone)})"
            )
        if draft.rejected_at is not None:
            lines.append(
                "Отклонен: "
                f"<b>{escape(self._format_datetime(draft.rejected_at))}</b> "
                f"({escape(self._settings.moderation_timezone)})"
            )
        if self._article_store is not None:
            latest_comment = self._article_store.latest_comment(draft_id=draft.id)
            if latest_comment is not None:
                lines.append(
                    "Комментарий: "
                    f"<code>{escape(self._short_error(latest_comment.body))}</code>"
                )
        if draft.last_error:
            lines.append(f"Ошибка: <code>{escape(self._short_error(draft.last_error))}</code>")
        return "\n".join(lines)

    def _keyboard_for_status(self, draft: ModerationDraftRecord) -> dict[str, Any]:
        if draft.status == "pending_review":
            return {
                "inline_keyboard": [
                    [
                        {
                            "text": "Опубликовать сейчас",
                            "callback_data": f"draft:publish:{draft.id}",
                        },
                        {
                            "text": "Запланировать",
                            "callback_data": f"draft:schedule:{draft.id}",
                        },
                    ],
                    [
                        {
                            "text": "Отклонить",
                            "callback_data": f"draft:reject:{draft.id}",
                        }
                    ],
                ]
            }
        if draft.status == "awaiting_schedule":
            return {
                "inline_keyboard": [
                    [
                        {
                            "text": "Опубликовать сейчас",
                            "callback_data": f"draft:publish:{draft.id}",
                        }
                    ],
                    [
                        {
                            "text": "Отменить ввод времени",
                            "callback_data": f"draft:reset:{draft.id}",
                        },
                        {
                            "text": "Отклонить",
                            "callback_data": f"draft:reject:{draft.id}",
                        },
                    ],
                ]
            }
        if draft.status == "awaiting_rejection_comment":
            return {
                "inline_keyboard": [
                    [
                        {
                            "text": "Отменить отклонение",
                            "callback_data": f"draft:cancel_reject:{draft.id}",
                        }
                    ]
                ]
            }
        return {"inline_keyboard": []}

    def _schedule_prompt(self, *, invalid_value: str | None = None) -> str:
        prefix = ""
        if invalid_value is not None:
            prefix = (
                "Не удалось разобрать время "
                f"<code>{escape(invalid_value)}</code>. Попробуйте еще раз.\n\n"
            )
        return (
            f"{prefix}"
            "Отправьте время публикации одним сообщением.\n"
            "Примеры:\n"
            "<code>2026-03-23 10:30</code>\n"
            "<code>23.03.2026 10:30</code>\n"
            "<code>10:30</code>\n"
            "<code>2026-03-23T10:30:00+03:00</code>\n\n"
            f"Часовой пояс по умолчанию: <b>{escape(self._settings.moderation_timezone)}</b>.\n"
            "Чтобы выйти из режима ввода времени, отправьте <code>/cancel</code>."
        )

    def _rejection_prompt(self) -> str:
        return (
            "Отправьте комментарий к отклонению одним сообщением.\n"
            "Комментарий будет сохранен в БД и доступен через API.\n\n"
            "Если хотите автоматически править статью на старте, используйте инструкции в формате:\n"
            "<code>title: Новый заголовок</code>\n"
            "<code>replace: старый текст => новый текст</code>\n"
            "<code>delete: фрагмент</code>\n"
            "<code>append: новый абзац</code>\n"
            "<code>prepend: вводный абзац</code>\n\n"
            "Чтобы выйти из режима отклонения, отправьте <code>/cancel</code>."
        )

    def _parse_publish_time(self, raw_value: str) -> datetime | None:
        value = raw_value.strip()
        if not value:
            return None

        now = datetime.now(self._timezone)

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=self._timezone)
            return self._ensure_future(parsed)

        for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
            try:
                parsed = datetime.strptime(value, fmt).replace(tzinfo=self._timezone)
            except ValueError:
                continue
            return self._ensure_future(parsed)

        try:
            parsed_time = datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None
        candidate = datetime.combine(now.date(), parsed_time, tzinfo=self._timezone)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    def _ensure_future(self, value: datetime) -> datetime | None:
        if value.astimezone(UTC) <= datetime.now(UTC):
            return None
        return value.astimezone(UTC)

    def _format_datetime(self, value: datetime) -> str:
        return value.astimezone(self._timezone).strftime("%d.%m.%Y %H:%M")

    def _is_allowed_user(self, user_id: int) -> bool:
        allowed_user_ids = self._settings.moderation_allowed_user_ids
        if not allowed_user_ids:
            return True
        return user_id in allowed_user_ids

    @staticmethod
    def _short_error(error: Exception | str) -> str:
        message = str(error).strip() or "unknown error"
        if len(message) <= 180:
            return message
        return f"{message[:177]}..."


class ModerationBotRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        moderation_publisher: TelegramPublisher,
        moderation_service: ModerationService,
    ) -> None:
        self._settings = settings
        self._moderation_publisher = moderation_publisher
        self._moderation_service = moderation_service
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._offset: int | None = None

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="moderation-bot-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = await self._moderation_publisher.get_updates(
                    offset=self._offset,
                    timeout=self._settings.moderation_poll_timeout_seconds,
                    allowed_updates=["callback_query", "message"],
                )
            except Exception:
                if await self._wait_or_stop():
                    break
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._offset = update_id + 1
                try:
                    await self._moderation_service.handle_update(update)
                except Exception:
                    continue

            if not updates and await self._wait_or_stop():
                break

    async def _wait_or_stop(self) -> bool:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=self._settings.moderation_poll_interval_seconds,
            )
        except TimeoutError:
            return False
        return True
