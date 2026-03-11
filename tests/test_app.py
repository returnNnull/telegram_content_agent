from fastapi.testclient import TestClient

from telegram_content_agent.config import get_settings
from telegram_content_agent.main import app


def _reset_settings() -> None:
    get_settings.cache_clear()


def test_health_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    _reset_settings()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "channel_id": "@channel"}
    _reset_settings()


def test_publish_requires_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    _reset_settings()

    with TestClient(app) as client:
        response = client.post("/publish", json={"text": "hello", "dry_run": True})

    assert response.status_code == 401
    _reset_settings()


def test_publish_dry_run_with_buttons(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    _reset_settings()

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


def test_publish_dry_run_media_group(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@channel")
    monkeypatch.setenv("PUBLISH_API_TOKEN", "secret-token")
    _reset_settings()

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
