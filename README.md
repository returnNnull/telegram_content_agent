# telegram-content-agent

Сервис для публикации постов в Telegram-канал через Bot API.

Поддерживает:
- текст
- ссылки
- одну или несколько картинок
- dry-run режим без отправки

## Что есть в проекте

- HTTP API на FastAPI
- отправка в канал через `sendMessage`, `sendPhoto`, `sendMediaGroup`
- поддержка ссылок как inline-кнопок или как блока ссылок в тексте
- публикация по умолчанию в канал из переменных окружения

## Быстрый старт

1. Создай и заполни `.env` по примеру `.env.example`
2. Установи зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

3. Запусти сервер:

```bash
telegram-content-agent
```

Или так:

```bash
uvicorn telegram_content_agent.main:app --host 0.0.0.0 --port 8000
```

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
- `TELEGRAM_CHANNEL_ID` — ID канала или `@channel_username`
- `PUBLISH_API_TOKEN` — bearer token для защиты `POST /publish`
- `TELEGRAM_API_BASE` — базовый URL Telegram API
- `REQUEST_TIMEOUT_SECONDS` — timeout HTTP-запросов
- `DEFAULT_PARSE_MODE` — сейчас поддержан `HTML` или пустое значение
- `DEFAULT_LINK_STYLE` — `buttons` или `text`

## API

### `GET /health`

Проверка, что сервер поднялся и прочитал конфигурацию.

### `POST /publish`

Публикация поста в канал.

Пример с текстом, картинками и ссылками:

```bash
curl -X POST http://localhost:8000/publish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer replace_with_long_random_token" \
  -d '{
    "text": "<b>Новый пост</b>\n\nРазобрали свежие изменения в проекте.",
    "parse_mode": "HTML",
    "link_style": "buttons",
    "links": [
      {
        "title": "GitHub",
        "url": "https://github.com/example/project/pull/123"
      },
      {
        "title": "Документация",
        "url": "https://example.com/docs"
      }
    ],
    "images": [
      {
        "url": "https://images.unsplash.com/photo-1516117172878-fd2c41f4a759"
      },
      {
        "path": "/absolute/path/to/local-image.png"
      }
    ]
  }'
```

Пример dry-run без публикации:

```bash
curl -X POST http://localhost:8000/publish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer replace_with_long_random_token" \
  -d '{
    "text": "Проверка публикации",
    "dry_run": true,
    "links": [
      {
        "title": "Репозиторий",
        "url": "https://github.com/example/project"
      }
    ]
  }'
```

## Поведение при отправке

- Если картинок нет, сервер вызывает `sendMessage`
- Если картинка одна, сервер вызывает `sendPhoto`
- Если картинок несколько, сервер вызывает `sendMediaGroup`
- Если текст длиннее лимита caption в Telegram, сервер отправляет картинки и отдельное сообщение с текстом
- Если ссылки переданы как `buttons`, они отправляются как inline-кнопки
- Если ссылки переданы как `text`, они добавляются в конец текста поста

## Документация

- [Спецификация](docs/spec.md)
- [Сервер и API](docs/server.md)
- [Деплой и GitHub Actions](docs/deployment.md)
