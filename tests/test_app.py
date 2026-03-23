import time
from datetime import UTC, datetime, timedelta
from itertools import count
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from telegram_content_agent.config import get_settings
from telegram_content_agent.main import app
from telegram_content_agent.telegram_client import TelegramPublisher


def _reset_settings() -> None:
    get_settings.cache_clear()


def _configure_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "publisher-token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("MODERATION_BOT_TOKEN", "moderation-token")
    monkeypatch.setenv("MODERATION_CHAT_ID", "-100900")
    monkeypatch.setenv("MODERATION_ALLOWED_USER_IDS", "777")
    monkeypatch.setenv("MODERATION_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("MODERATION_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MODERATION_POLL_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "scheduled.sqlite3"))
    monkeypatch.setenv("SCHEDULER_POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("SCHEDULER_RETRY_DELAY_SECONDS", "0.05")
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


def _load_draft(client: TestClient, draft_id: str) -> dict:
    return client.get(
        f"/drafts/{draft_id}",
        headers={"Authorization": "Bearer secret-token"},
    ).json()


def _load_scheduled_post(client: TestClient, post_id: str) -> dict:
    return client.get(
        f"/schedule/{post_id}",
        headers={"Authorization": "Bearer secret-token"},
    ).json()


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


def test_publish_dry_run_with_buttons(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/publish",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "text": "<b>Тест</b>",
                "parse_mode": "HTML",
                "dry_run": True,
                "links": [
                    {
                        "title": "Репозиторий",
                        "url": "https://github.com/example/project",
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["strategy"] == "message"
    assert payload["actions"][0]["method"] == "sendMessage"
    assert payload["actions"][0]["payload"]["reply_markup"]["inline_keyboard"][0][0]["text"] == (
        "Репозиторий"
    )
    _reset_settings()


def test_publish_dry_run_media_group(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/publish",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "text": "Пост с картинками",
                "dry_run": True,
                "images": [
                    {"url": "https://example.com/1.png"},
                    {"url": "https://example.com/2.png"},
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "media-group"
    assert payload["actions"][0]["method"] == "sendMediaGroup"
    _reset_settings()


def test_submit_draft_creates_pending_review(monkeypatch, tmp_path) -> None:
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
        response = client.post(
            "/drafts",
            headers={"Authorization": "Bearer secret-token"},
            json={"text": "Новая статья"},
        )

        assert response.status_code == 200
        created = response.json()
        assert created["status"] == "pending_review"

        list_response = client.get(
            "/drafts",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert list_response.status_code == 200
    drafts = list_response.json()
    assert len(drafts) == 1
    assert drafts[0]["id"] == created["id"]
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert transport["sent_messages"][0]["chat_id"] == "-100900"
    keyboard = transport["sent_messages"][0]["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["text"] == "Опубликовать сейчас"
    _reset_settings()


def test_moderation_callback_publishes_to_channel(monkeypatch, tmp_path) -> None:
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
            "actions": [
                {
                    "method": "sendMessage",
                    "payload": {"chat_id": self._default_chat_id, "text": request.text},
                }
            ],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    with TestClient(app) as client:
        created = client.post(
            "/drafts",
            headers={"Authorization": "Bearer secret-token"},
            json={"text": "Статья для публикации"},
        ).json()
        draft_id = created["id"]

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

        def published_draft() -> dict | None:
            draft = _load_draft(client, draft_id)
            if draft["status"] == "published":
                return draft
            return None

        published = _wait_until(published_draft)

    assert published["status"] == "published"
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert publish_calls[1]["default_chat_id"] == "@channel"
    assert transport["callback_answers"][-1]["text"] == "Опубликовано в основной канал."
    assert "Статус: <b>опубликован</b>" in transport["edited_messages"][-1]["text"]
    _reset_settings()


def test_moderation_schedule_and_scheduler_publish(monkeypatch, tmp_path) -> None:
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
            "/drafts",
            headers={"Authorization": "Bearer secret-token"},
            json={"text": "Запланированная статья"},
        ).json()
        draft_id = created["id"]

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

        def awaiting_schedule_draft() -> dict | None:
            draft = _load_draft(client, draft_id)
            if draft["status"] == "awaiting_schedule":
                return draft
            return None

        awaiting_schedule = _wait_until(awaiting_schedule_draft)
        assert awaiting_schedule["status"] == "awaiting_schedule"

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

        def scheduled_draft() -> dict | None:
            draft = _load_draft(client, draft_id)
            if draft["status"] == "scheduled":
                return draft
            return None

        scheduled = _wait_until(scheduled_draft)
        assert scheduled["scheduled_post_id"]

        def published_draft() -> dict | None:
            draft = _load_draft(client, draft_id)
            if draft["status"] == "published":
                return draft
            return None

        published = _wait_until(published_draft, timeout=4.0)

    assert published["status"] == "published"
    assert publish_calls[0]["default_chat_id"] == "-100900"
    assert publish_calls[-1]["default_chat_id"] == "@channel"
    assert "Статус: <b>запланирован</b>" in transport["edited_messages"][-2]["text"]
    assert "Статус: <b>опубликован</b>" in transport["edited_messages"][-1]["text"]
    _reset_settings()


def test_schedule_post_and_list(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    publish_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    with TestClient(app) as client:
        create_response = client.post(
            "/schedule",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "text": "Отложенный пост",
                "publish_at": publish_at,
            },
        )

        assert create_response.status_code == 200
        created = create_response.json()
        assert created["status"] == "pending"
        assert created["request"]["text"] == "Отложенный пост"

        list_response = client.get(
            "/schedule",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert list_response.status_code == 200
    scheduled_posts = list_response.json()
    assert len(scheduled_posts) == 1
    assert scheduled_posts[0]["id"] == created["id"]
    _reset_settings()


def test_scheduler_publishes_due_post(monkeypatch, tmp_path) -> None:
    _configure_env(monkeypatch, tmp_path)
    _stub_moderation_transport(monkeypatch)

    async def fake_publish(self, request):
        return {
            "ok": True,
            "strategy": "message",
            "rendered_text": request.text,
            "actions": [{"method": "sendMessage", "payload": {"text": request.text}}],
            "telegram_results": [],
        }

    monkeypatch.setattr(TelegramPublisher, "publish", fake_publish)

    publish_at = (datetime.now(UTC) + timedelta(milliseconds=100)).isoformat()

    with TestClient(app) as client:
        create_response = client.post(
            "/schedule",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "text": "Нужно отправить позже",
                "publish_at": publish_at,
            },
        )
        assert create_response.status_code == 200
        post_id = create_response.json()["id"]

        def published_post() -> dict | None:
            post = _load_scheduled_post(client, post_id)
            if post["status"] == "published":
                return post
            return None

        current = _wait_until(published_post)

    assert current["status"] == "published"
    assert current["attempts"] == 1
    assert current["last_result"]["strategy"] == "message"
    _reset_settings()
