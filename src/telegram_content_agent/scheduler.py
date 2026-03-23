from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from inspect import isawaitable
from typing import Any, Awaitable, Callable
from uuid import uuid4

from telegram_content_agent.config import Settings
from telegram_content_agent.models import PublishRequest, ScheduledPostResponse
from telegram_content_agent.telegram_client import TelegramPublisher


class ScheduledPostNotFoundError(RuntimeError):
    """Raised when a scheduled post does not exist."""


class ScheduledPostConflictError(RuntimeError):
    """Raised when a scheduled post cannot transition to the requested state."""


class ScheduledPostStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        if not self._db_path.is_absolute():
            self._db_path = self._db_path.resolve()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    publish_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT
                )
                """
            )
            connection.commit()

    def recover_processing_posts(self) -> None:
        now = self._iso(datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_posts
                SET status = 'pending',
                    updated_at = ?
                WHERE status = 'processing'
                """,
                (now,),
            )
            connection.commit()

    def create(self, request: PublishRequest, publish_at: datetime) -> ScheduledPostResponse:
        now = datetime.now(UTC)
        post_id = uuid4().hex
        sanitized_request = request.model_copy(update={"dry_run": False})
        row = {
            "id": post_id,
            "status": "pending",
            "publish_at": self._iso(publish_at),
            "next_attempt_at": self._iso(publish_at),
            "created_at": self._iso(now),
            "updated_at": self._iso(now),
            "published_at": None,
            "attempts": 0,
            "last_error": None,
            "request_json": sanitized_request.model_dump_json(),
            "result_json": None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_posts (
                    id, status, publish_at, next_attempt_at, created_at, updated_at,
                    published_at, attempts, last_error, request_json, result_json
                ) VALUES (
                    :id, :status, :publish_at, :next_attempt_at, :created_at, :updated_at,
                    :published_at, :attempts, :last_error, :request_json, :result_json
                )
                """,
                row,
            )
            connection.commit()
        return self.get(post_id)

    def list(self, *, status: str | None = None) -> list[ScheduledPostResponse]:
        query = """
            SELECT *
            FROM scheduled_posts
        """
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY publish_at ASC, created_at ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get(self, post_id: str) -> ScheduledPostResponse:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
        if row is None:
            raise ScheduledPostNotFoundError(f"Scheduled post not found: {post_id}")
        return self._row_to_model(row)

    def cancel(self, post_id: str) -> ScheduledPostResponse:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM scheduled_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if row is None:
                raise ScheduledPostNotFoundError(f"Scheduled post not found: {post_id}")
            if row["status"] not in {"pending", "failed"}:
                raise ScheduledPostConflictError(
                    "Only pending or failed scheduled posts can be canceled."
                )
            connection.execute(
                """
                UPDATE scheduled_posts
                SET status = 'canceled',
                    updated_at = ?
                WHERE id = ?
                """,
                (self._iso(datetime.now(UTC)), post_id),
            )
            connection.commit()
        return self.get(post_id)

    def claim_due_posts(self, *, now: datetime, limit: int) -> list[ScheduledPostResponse]:
        due_at = self._iso(now)
        claimed_ids: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM scheduled_posts
                WHERE status = 'pending'
                  AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC, created_at ASC
                LIMIT ?
                """,
                (due_at, limit),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE scheduled_posts
                    SET status = 'processing',
                        updated_at = ?
                    WHERE id = ?
                      AND status = 'pending'
                    """,
                    (due_at, row["id"]),
                )
                if updated.rowcount:
                    claimed_ids.append(row["id"])
            connection.commit()
            if not claimed_ids:
                return []
            placeholders = ",".join("?" for _ in claimed_ids)
            claimed_rows = connection.execute(
                f"SELECT * FROM scheduled_posts WHERE id IN ({placeholders}) ORDER BY next_attempt_at ASC, created_at ASC",
                tuple(claimed_ids),
            ).fetchall()
        return [self._row_to_model(row) for row in claimed_rows]

    def mark_published(self, post_id: str, result: dict[str, Any]) -> ScheduledPostResponse:
        now = datetime.now(UTC)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT attempts FROM scheduled_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if current is None:
                raise ScheduledPostNotFoundError(f"Scheduled post not found: {post_id}")
            connection.execute(
                """
                UPDATE scheduled_posts
                SET status = 'published',
                    updated_at = ?,
                    published_at = ?,
                    attempts = ?,
                    last_error = NULL,
                    result_json = ?
                WHERE id = ?
                """,
                (
                    self._iso(now),
                    self._iso(now),
                    current["attempts"] + 1,
                    json.dumps(result, ensure_ascii=False),
                    post_id,
                ),
            )
            connection.commit()
        return self.get(post_id)

    def mark_failed(
        self,
        post_id: str,
        *,
        error_message: str,
        retry_delay_seconds: float,
        max_attempts: int,
    ) -> ScheduledPostResponse:
        now = datetime.now(UTC)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT attempts FROM scheduled_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if current is None:
                raise ScheduledPostNotFoundError(f"Scheduled post not found: {post_id}")
            attempts = current["attempts"] + 1
            if attempts >= max_attempts:
                status = "failed"
                next_attempt_at = now
            else:
                status = "pending"
                next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
            connection.execute(
                """
                UPDATE scheduled_posts
                SET status = ?,
                    updated_at = ?,
                    next_attempt_at = ?,
                    attempts = ?,
                    last_error = ?,
                    result_json = NULL
                WHERE id = ?
                """,
                (
                    status,
                    self._iso(now),
                    self._iso(next_attempt_at),
                    attempts,
                    error_message,
                    post_id,
                ),
            )
            connection.commit()
        return self.get(post_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_model(self, row: sqlite3.Row) -> ScheduledPostResponse:
        return ScheduledPostResponse(
            id=row["id"],
            status=row["status"],
            publish_at=self._parse_datetime(row["publish_at"]),
            next_attempt_at=self._parse_datetime(row["next_attempt_at"]),
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
            published_at=self._parse_optional_datetime(row["published_at"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            request=PublishRequest.model_validate_json(row["request_json"]),
            last_result=json.loads(row["result_json"]) if row["result_json"] else None,
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


class ScheduledPublisher:
    def __init__(
        self,
        *,
        settings: Settings,
        publisher: TelegramPublisher,
        store: ScheduledPostStore,
        on_post_published: Callable[[ScheduledPostResponse], Awaitable[None] | None] | None = None,
        on_post_failed: Callable[[ScheduledPostResponse], Awaitable[None] | None] | None = None,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._store = store
        self._on_post_published = on_post_published
        self._on_post_failed = on_post_failed
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="scheduled-post-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    def set_event_handlers(
        self,
        *,
        on_post_published: Callable[[ScheduledPostResponse], Awaitable[None] | None] | None,
        on_post_failed: Callable[[ScheduledPostResponse], Awaitable[None] | None] | None,
    ) -> None:
        self._on_post_published = on_post_published
        self._on_post_failed = on_post_failed

    def create(self, request: PublishRequest, publish_at: datetime) -> ScheduledPostResponse:
        return self._store.create(request=request, publish_at=publish_at)

    def list(self, *, status: str | None = None) -> list[ScheduledPostResponse]:
        return self._store.list(status=status)

    def get(self, post_id: str) -> ScheduledPostResponse:
        return self._store.get(post_id)

    def cancel(self, post_id: str) -> ScheduledPostResponse:
        return self._store.cancel(post_id)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now(UTC)
            due_posts = self._store.claim_due_posts(
                now=now,
                limit=self._settings.scheduler_batch_size,
            )
            for post in due_posts:
                request = post.request.model_copy(update={"dry_run": False})
                try:
                    result = await self._publisher.publish(request)
                except Exception as error:  # pragma: no cover - exercised through state
                    failed_post = self._store.mark_failed(
                        post.id,
                        error_message=str(error),
                        retry_delay_seconds=self._settings.scheduler_retry_delay_seconds,
                        max_attempts=self._settings.scheduler_max_attempts,
                    )
                    if failed_post.status == "failed":
                        await self._emit(self._on_post_failed, failed_post)
                else:
                    published_post = self._store.mark_published(post.id, result)
                    await self._emit(self._on_post_published, published_post)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.scheduler_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    @staticmethod
    async def _emit(
        callback: Callable[[ScheduledPostResponse], Awaitable[None] | None] | None,
        post: ScheduledPostResponse,
    ) -> None:
        if callback is None:
            return
        result = callback(post)
        if isawaitable(result):
            await result
