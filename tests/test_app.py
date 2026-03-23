import time
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from telegram_content_agent.articles import LocalArticleDocument, LocalArticleRepository
from telegram_content_agent.config import get_settings
from telegram_content_agent.main import app
from telegram_content_agent.telegram_client import TelegramPublisher


def _reset_settings() -> None:
    get_settings.cache_clear()


def _configure_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "publisher-token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("MODERATION_BOT_TOKEN", "moderation-token")
    monkeypatch.setenv("MODERATION_CHAT_ID", "-100900")
    monkeypatch.setenv("MODERATION_ALLOWED_USER_IDS", "777")
    monkeypatch.setenv("MODERATION_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("MODERATION_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MODERATION_POLL_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("SCHEDULER_POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("SCHEDULER_RETRY_DELAY_SECONDS", "0.05")
    monkeypatch.setenv("SCHEDULER_MAX_ATTEMPTS", "2")
    _reset_settings()


def _stub_moderation_transport(monkeypatch) -> dict[str, list]:
    updates_queue: list[dict] = []
    sent_messages: list[dict] = []
    edited_messages: list[dict] = []
    callback_answers: list[dict] = []
    message_ids = count(100)

    async def fake_get_updates(self, *, offset=None, timeout=20, allowed_updates=None):
        ready: list[dict] = []
        remaining: list[dict] = []
        for update in updates_queue:
            update_id = update.get("update_id")
            if offset is not None and isinstance(update_id, int) and update_id < offset:
                remaining.append(update)
                continue
            ready.append(update)
        updates_queue[:] = remaining
        return ready

    async def fake_send_message(self, **kwargs):
        sent_messages.append(kwargs)
        return {
            "ok": True,
            "result": {
                "message_id": next(message_ids),
                "chat": {"id": kwargs["chat_id"]},
                "text": kwargs["text"],
            },
        }

    async def fake_edit_message_text(self, **kwargs):
        edited_messages.append(kwargs)
        return {
            "ok": True,
            "result": {
                "message_id": kwargs["message_id"],
                "chat": {"id": kwargs["chat_id"]},
                "text": kwargs["text"],
            },
        }

    async def fake_answer_callback_query(self, **kwargs):
        callback_answers.append(kwargs)
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramPublisher, "get_updates", fake_get_updates)
    monkeypatch.setattr(TelegramPublisher, "send_message", fake_send_message)
    monkeypatch.setattr(TelegramPublisher, "edit_message_text", fake_edit_message_text)
    monkeypatch.setattr(TelegramPublisher, "answer_callback_query", fake_answer_callback_query)
    return {
        "updates_queue": updates_queue,
        "sent_messages": sent_messages,
        "edited_messages": edited_messages,
        "callback_answers": callback_answers,
    }


def _wait_until(predicate, timeout: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for condition.")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer secret-token"}


def _load_article(client: TestClient, article_id: str) -> dict:
    return client.get(f"/articles/{article_id}", headers=_auth_headers()).json()


def _load_draft(client: TestClient, draft_id: str) -> dict:
    return client.get(f"/drafts/{draft_id}", headers=_auth_headers()).json()


def _load_scheduled_post(client: TestClient, post_id: str) -> dict:
    return client.get(f"/schedule/{post_id}", headers=_auth_headers()).json()


def _article_snapshot(
    *,
    article_id: str | None = None,
    title: str = "Новая статья",
    slug: str = "new-article",
    markdown: str = "# Новая статья\n\nТекст статьи.\n",
    text: str | None = None,
    moderation_comment: str | None = None,
) -> dict:
    return {
        "article_id": article_id,
        "title": title,
        "slug": slug,
        "markdown": markdown,
        "cover_path": "publications/InComedy/post-assets/new-article-cover.png",
        "payload_path": ".codex-local/payloads/new-article.json",
        "source_refs": ["https://example.com/spec"],
        "attached_links": ["https://example.com/pr/1"],
        "moderation_comment": moderation_comment,
        "payload": {
            "text": text or title,
            "parse_mode": "HTML",
            "link_style": "buttons",
        },
    }


def test_health_endpoint(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["channel_id"] == "@channel"
    assert payload["moderation_chat_id"] == "-100900"
    assert payload["server_time"]
    _reset_settings()


def test_publish_requires_bearer_token(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        response = client.post("/publish", json={"text": "hello", "dry_run": True})

    assert response.status_code == 401
    _reset_settings()


def test_article_dry_run_without_article_id_creates_record(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(),
        )

        assert response.status_code == 200
        payload = response.json()
        article_id = payload["article"]["article_id"]
        assert article_id
        assert payload["article"]["status"] == "draft"
        assert payload["article"]["publish_strategy"] == "message"
        fetched = _load_article(client, article_id)

    assert fetched["article_id"] == article_id
    assert fetched["title"] == "Новая статья"
    _reset_settings()


def test_article_dry_run_with_existing_article_id_updates_snapshot(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        first = client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(title="Первая версия", text="Первая версия"),
        ).json()
        article_id = first["article"]["article_id"]

        second = client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(
                article_id=article_id,
                title="Вторая версия",
                text="Вторая версия",
                markdown="# Вторая версия\n\nОбновленный текст.\n",
            ),
        )

        assert second.status_code == 200
        payload = second.json()
        fetched = _load_article(client, article_id)

    assert payload["article"]["article_id"] == article_id
    assert fetched["title"] == "Вторая версия"
    assert fetched["payload"]["text"] == "Вторая версия"
    _reset_settings()


def test_submit_endpoint_creates_article_record_and_pending_review(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)
    publish_calls: list[dict] = []

    async def fake_publish(self, request):
        publish_calls.append(
            {
                "default_chat_id": self._default_chat_id,
                "request_chat_id": request.chat_id,
                "text": request.text,
                "dry_run": request.dry_run,
            }
        )
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        response = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(),
        )

        assert response.status_code == 200
        payload = response.json()
        article_id = payload["article"]["article_id"]
        draft_id = payload["draft"]["id"]
        article = _load_article(client, article_id)
        draft = _load_draft(client, draft_id)

    assert article["status"] == "pending_review"
    assert article["current_draft_id"] == draft_id
    assert draft["article_id"] == article_id
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert transport["sent_messages"][0]["chat_id"] == "-100900"
    _reset_settings()


def test_publish_now_updates_article_by_stable_article_id(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)
    publish_calls: list[dict] = []

    async def fake_publish(self, request):
        publish_calls.append(
            {
                "default_chat_id": self._default_chat_id,
                "request_chat_id": request.chat_id,
                "text": request.text,
            }
        )
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        created = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(),
        ).json()
        article_id = created["article"]["article_id"]
        draft_id = created["draft"]["id"]

        transport["updates_queue"].append(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": f"draft:publish:{draft_id}",
                    "message": {"message_id": 100, "chat": {"id": -100900}},
                },
            }
        )

        article = _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "published"
            else None
        )

    assert article["article_id"] == article_id
    assert article["status"] == "published"
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert publish_calls[-1]["default_chat_id"] == "@channel"
    assert transport["callback_answers"][-1]["text"] == "Опубликовано в основной канал."
    _reset_settings()


def test_schedule_and_subsequent_publication(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)
    publish_calls: list[dict] = []

    async def fake_publish(self, request):
        publish_calls.append(
            {
                "default_chat_id": self._default_chat_id,
                "request_chat_id": request.chat_id,
                "text": request.text,
            }
        )
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        created = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(title="Запланированная статья", text="Запланированная статья"),
        ).json()
        article_id = created["article"]["article_id"]
        draft_id = created["draft"]["id"]

        transport["updates_queue"].append(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": f"draft:schedule:{draft_id}",
                    "message": {"message_id": 100, "chat": {"id": -100900}},
                },
            }
        )

        _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "awaiting_schedule"
            else None
        )

        future_local = (
            datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(seconds=1)
        ).isoformat()
        transport["updates_queue"].append(
            {
                "update_id": 2,
                "message": {
                    "message_id": 200,
                    "chat": {"id": -100900},
                    "from": {"id": 777},
                    "text": future_local,
                },
            }
        )

        scheduled = _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "scheduled"
            else None
        )
        scheduled_post_id = _load_draft(client, draft_id)["scheduled_post_id"]
        assert scheduled_post_id

        published = _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "published"
            else None,
            timeout=4.0,
        )
        scheduled_post = _load_scheduled_post(client, scheduled_post_id)

    assert scheduled["status"] == "scheduled"
    assert published["status"] == "published"
    assert scheduled_post["status"] == "published"
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert publish_calls[-1]["default_chat_id"] == "@channel"
    _reset_settings()


def test_reject_saves_comment_and_updates_article(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)

    async def fake_publish(self, request):
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        created = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(title="Статья", text="Статья"),
        ).json()
        article_id = created["article"]["article_id"]
        draft_id = created["draft"]["id"]

        transport["updates_queue"].append(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": f"draft:reject:{draft_id}",
                    "message": {"message_id": 100, "chat": {"id": -100900}},
                },
            }
        )

        _wait_until(
            lambda: current
            if (current := _load_draft(client, draft_id))["status"] == "awaiting_rejection_comment"
            else None
        )

        transport["updates_queue"].append(
            {
                "update_id": 2,
                "message": {
                    "message_id": 200,
                    "chat": {"id": -100900},
                    "from": {"id": 777},
                    "text": "Нужно сильнее сократить вводный абзац",
                },
            }
        )

        article = _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "rejected"
            else None
        )
        draft_comments = client.get(
            f"/drafts/{draft_id}/comments",
            headers=_auth_headers(),
        ).json()
        article_comments = client.get(
            f"/articles/{article_id}/comments",
            headers=_auth_headers(),
        ).json()

    assert article["moderation_comment"] == "Нужно сильнее сократить вводный абзац"
    assert draft_comments[0]["body"] == "Нужно сильнее сократить вводный абзац"
    assert article_comments[0]["id"] == draft_comments[0]["id"]
    _reset_settings()


def test_restart_preserves_article_snapshot_and_scheduled_publish(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)

    async def fake_publish(self, request):
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        dry_run = client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(title="Переживет рестарт", text="Переживет рестарт"),
        ).json()
        article_id = dry_run["article"]["article_id"]
        created = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(
                article_id=article_id,
                title="Переживет рестарт",
                text="Переживет рестарт",
            ),
        ).json()
        draft_id = created["draft"]["id"]

        transport["updates_queue"].append(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": f"draft:schedule:{draft_id}",
                    "message": {"message_id": 100, "chat": {"id": -100900}},
                },
            }
        )
        _wait_until(
            lambda: current
            if (current := _load_draft(client, draft_id))["status"] == "awaiting_schedule"
            else None
        )
        future_local = (
            datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(seconds=1)
        ).isoformat()
        transport["updates_queue"].append(
            {
                "update_id": 2,
                "message": {
                    "message_id": 200,
                    "chat": {"id": -100900},
                    "from": {"id": 777},
                    "text": future_local,
                },
            }
        )
        scheduled_post_id = _wait_until(
            lambda: current
            if (current := _load_draft(client, draft_id))["status"] == "scheduled"
            else None
        )["scheduled_post_id"]

    transport = _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        article_after_restart = _load_article(client, article_id)
        assert article_after_restart["markdown"] == "# Новая статья\n\nТекст статьи.\n"
        assert article_after_restart["payload"]["text"] == "Переживет рестарт"
        published = _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "published"
            else None,
            timeout=4.0,
        )
        scheduled_post = _load_scheduled_post(client, scheduled_post_id)

    assert published["status"] == "published"
    assert scheduled_post["status"] == "published"
    _reset_settings()


def test_server_does_not_auto_scan_local_articles_on_startup(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    legacy_article = tmp_path / "publications" / "InComedy" / "articles" / "legacy.md"
    legacy_article.parent.mkdir(parents=True, exist_ok=True)
    legacy_article.write_text("# Legacy\n\nНе должно автоотправиться.\n", encoding="utf-8")

    with TestClient(app) as client:
        articles = client.get("/articles", headers=_auth_headers()).json()
        drafts = client.get("/drafts", headers=_auth_headers()).json()

    assert articles == []
    assert drafts == []
    _reset_settings()


def test_resubmitting_updated_article_uses_same_article_id(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    transport = _stub_moderation_transport(monkeypatch)

    async def fake_publish(self, request):
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        dry_run = client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(title="Первая", text="Первая"),
        ).json()
        article_id = dry_run["article"]["article_id"]

        first_submit = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(article_id=article_id, title="Первая", text="Первая"),
        ).json()
        first_draft_id = first_submit["draft"]["id"]

        transport["updates_queue"].append(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": f"draft:reject:{first_draft_id}",
                    "message": {"message_id": 100, "chat": {"id": -100900}},
                },
            }
        )
        _wait_until(
            lambda: current
            if (current := _load_draft(client, first_draft_id))["status"] == "awaiting_rejection_comment"
            else None
        )
        transport["updates_queue"].append(
            {
                "update_id": 2,
                "message": {
                    "message_id": 200,
                    "chat": {"id": -100900},
                    "from": {"id": 777},
                    "text": "Нужен новый ракурс",
                },
            }
        )
        _wait_until(
            lambda: current
            if (current := _load_article(client, article_id))["status"] == "rejected"
            else None
        )

        client.post(
            "/articles/dry-run",
            headers=_auth_headers(),
            json=_article_snapshot(
                article_id=article_id,
                title="Вторая",
                text="Вторая",
                markdown="# Вторая\n\nОбновленный текст.\n",
                moderation_comment="Нужен новый ракурс",
            ),
        )
        second_submit = client.post(
            "/articles/submit",
            headers=_auth_headers(),
            json=_article_snapshot(
                article_id=article_id,
                title="Вторая",
                text="Вторая",
                markdown="# Вторая\n\nОбновленный текст.\n",
                moderation_comment="Нужен новый ракурс",
            ),
        ).json()
        drafts = client.get("/drafts", headers=_auth_headers()).json()
        article = _load_article(client, article_id)

    assert second_submit["article"]["article_id"] == article_id
    assert article["title"] == "Вторая"
    assert article["status"] == "pending_review"
    assert len(drafts) == 2
    assert all(draft["article_id"] == article_id for draft in drafts)
    _reset_settings()


def test_local_active_archive_index_workflow(tmp_path) -> None:
    repo = LocalArticleRepository(tmp_path / "publications" / "InComedy")
    now = datetime.now(UTC)
    article = LocalArticleDocument(
        article_id=None,
        status="draft",
        created_at=now,
        updated_at=now,
        scheduled_publish_at=None,
        moderation_comment=None,
        title="Локальная статья",
        slug="local-article",
        cover_path="publications/InComedy/post-assets/local-article.png",
        payload_path=".codex-local/payloads/local-article.json",
        source_refs=["https://example.com/spec"],
        attached_links=["https://example.com/pr/1"],
        last_synced_at=None,
        publish_strategy=None,
        last_error=None,
        body="# Локальная статья\n\nТекст.\n",
    )

    path = repo.save_article(article)
    index = repo.read_index()
    cards = repo.list_active_cards()
    loaded = repo.load_article(path)

    assert path == tmp_path / "publications" / "InComedy" / "articles" / "active" / "local-article.md"
    assert index["active"][0]["slug"] == "local-article"
    assert index["archive"] == []
    assert cards[0].path == "publications/InComedy/articles/active/local-article.md"
    assert loaded.title == "Локальная статья"


def test_published_local_article_moves_to_archive_and_leaves_active_manifest(tmp_path) -> None:
    repo = LocalArticleRepository(tmp_path / "publications" / "InComedy")
    now = datetime.now(UTC)
    active_path = repo.save_article(
        LocalArticleDocument(
            article_id="article-123",
            status="published",
            created_at=now,
            updated_at=now,
            scheduled_publish_at=None,
            moderation_comment=None,
            title="Архивируемая статья",
            slug="archived-article",
            cover_path="publications/InComedy/post-assets/archived-article.png",
            payload_path=".codex-local/payloads/archived-article.json",
            source_refs=[],
            attached_links=[],
            last_synced_at=None,
            publish_strategy="single-image",
            last_error=None,
            body="# Архивируемая статья\n\nТекст.\n",
        )
    )

    archived_path = repo.archive_article(article_id="article-123")
    index = repo.read_index()

    assert active_path.exists() is False
    assert archived_path == (
        tmp_path / "publications" / "InComedy" / "articles" / "archive" / "archived-article.md"
    )
    assert index["active"] == []
    assert index["archive"][0]["article_id"] == "article-123"
