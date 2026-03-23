# telegram-content-agent

Сервис для публикации Telegram-статей через два бота на одном сервере.

Новый основной flow:
- внешний агент или скрипт отправляет готовую статью в `POST /drafts`
- moderation bot публикует черновик в вашу группу
- в группе вы нажимаете `Опубликовать сейчас` или `Запланировать`
- при подтверждении publisher bot отправляет статью в основной канал
- при отложенной публикации время задается прямо в группе сообщением

Дополнительно сервис оставляет прямые `POST /publish` и `POST /schedule` как bypass для ручных или аварийных сценариев.

## Что есть в проекте

- FastAPI-сервер
- publisher bot для основного канала
- moderation bot для review-группы
- long polling для обработки callback и ввода времени без webhook
- SQLite для хранения draft-ов и отложенных публикаций
- scheduler с retry для отложенных постов
- поддержка текста, ссылок, одной или нескольких картинок

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

- `TELEGRAM_BOT_TOKEN` — токен publisher bot для основного канала
- `TELEGRAM_CHANNEL_ID` — ID канала или `@channel_username`
- `MODERATION_BOT_TOKEN` — токен moderation bot для review-чата
- `MODERATION_CHAT_ID` — ID группы, где вы подтверждаете публикации
- `MODERATION_TIMEZONE` — timezone для ввода времени в чате, по умолчанию `Europe/Moscow`
- `MODERATION_ALLOWED_USER_IDS` — список Telegram user id через запятую; если пусто, модерировать может любой участник `MODERATION_CHAT_ID`
- `MODERATION_POLL_INTERVAL_SECONDS` — пауза между циклами polling
- `MODERATION_POLL_TIMEOUT_SECONDS` — timeout одного `getUpdates`
- `PUBLISH_API_TOKEN` — bearer token для защищенных endpoint'ов
- `TELEGRAM_API_BASE` — базовый URL Telegram Bot API
- `REQUEST_TIMEOUT_SECONDS` — timeout HTTP-запросов к Telegram
- `DEFAULT_PARSE_MODE` — сейчас поддержан `HTML` или пустое значение
- `DEFAULT_LINK_STYLE` — `buttons` или `text`
- `SCHEDULER_DB_PATH` — путь к SQLite-базе runtime-состояния
- `SCHEDULER_POLL_INTERVAL_SECONDS` — как часто воркер проверяет due-задачи
- `SCHEDULER_BATCH_SIZE` — сколько задач забирать за один цикл
- `SCHEDULER_RETRY_DELAY_SECONDS` — задержка перед повторной попыткой
- `SCHEDULER_MAX_ATTEMPTS` — максимум попыток отправки отложенного поста

## API

### `GET /health`

Проверка, что сервер поднялся и прочитал конфигурацию обоих ботов.

### `POST /drafts`

Основной endpoint. Создает moderation draft, отправляет статью в review-группу и публикует control message с кнопками.

```bash
curl -X POST http://localhost:8000/drafts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer replace_with_long_random_token" \
  -d '{
    "text": "<b>Новая статья</b>\n\nЭто черновик для review.",
    "parse_mode": "HTML",
    "link_style": "buttons",
    "links": [
      {
        "title": "Спецификация",
        "url": "https://example.com/spec"
      }
    ],
    "images": [
      {
        "url": "https://example.com/preview.png"
      }
    ]
  }'
```

После этого moderation bot:
- отправит саму статью в `MODERATION_CHAT_ID`
- отдельным сообщением покажет статус черновика и кнопки
- по кнопке `Запланировать` попросит прислать время отдельным сообщением

Поддерживаемые форматы времени в чате:
- `2026-03-23 10:30`
- `23.03.2026 10:30`
- `10:30`
- `2026-03-23T10:30:00+03:00`

Отменить режим ввода времени можно сообщением `/cancel`.

### `GET /drafts`

Список черновиков. Можно фильтровать по `?status=pending_review`.

### `GET /drafts/{id}`

Детали одного moderation draft.

### `POST /publish`

Прямой publish в канал через publisher bot. Это обход moderation flow.

### `POST /schedule`

Прямое создание отложенной публикации в канал. Это тоже bypass-сценарий.

### `GET /schedule`

Список отложенных задач.

### `GET /schedule/{id}`

Детали одной отложенной публикации.

### `DELETE /schedule/{id}`

Отмена задачи в статусе `pending` или `failed`.

## Поведение сервиса

- moderation flow использует два разных bot token в одном процессе
- preview в review-группе уходит через moderation bot
- публикация в канал уходит через publisher bot
- если в группе выбрано время, scheduler публикует пост позже автоматически
- scheduler обновляет статус draft-а после фактической публикации
- если картинок нет, используется `sendMessage`
- если картинка одна, используется `sendPhoto`
- если картинок несколько, используется `sendMediaGroup`
- если caption не влезает, сервис отправит картинки и отдельное сообщение с текстом

## Документация

- [Спецификация](docs/spec.md)
- [Сервер и API](docs/server.md)
- [Деплой и GitHub Actions](docs/deployment.md)
- [Публикация из другого чата](docs/publishing-from-chat.md)
