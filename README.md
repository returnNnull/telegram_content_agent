# telegram-content-agent

Сервис для публикации Telegram-постов через review-группу и два Telegram-бота в одном FastAPI-процессе.

## Актуальный flow

1. При старте сервис сканирует `ARTICLES_ROOT_PATH/**/articles/*.md` и ищет статьи, которые еще не проходили модерацию.
2. Для новой статьи создается `article` в SQLite и связанный moderation draft.
3. `moderation bot` отправляет preview поста в `MODERATION_CHAT_ID` и отдельное control message.
4. Модератор в review-группе выбирает `Опубликовать сейчас`, `Запланировать` или `Отклонить`.
5. Для `Отклонить` бот запрашивает отдельный комментарий сообщением и сохраняет его в БД.
6. На следующем старте сервис повторно проверяет отклоненные статьи:
   - если markdown уже изменился вручную, статья отправляется повторно;
   - если комментарий содержит поддерживаемые edit-инструкции, сервис применяет их к markdown и отправляет статью повторно.
7. `publisher bot` публикует подтвержденный пост в `TELEGRAM_CHANNEL_ID` сразу или через scheduler.

Прямые `POST /publish` и `POST /schedule` оставлены как bypass для ручных, аварийных и технических сценариев.

## Что есть в проекте

- FastAPI-сервер
- publisher bot для основного канала
- moderation bot для review-группы
- long polling для обработки callback и ввода времени без webhook
- SQLite для хранения draft-ов и отложенных публикаций
- SQLite для хранения статей и комментариев модератора
- startup-sync локальных markdown-статей из `publications`
- scheduler с retry для отложенных постов
- поддержка текста, ссылок, одной или нескольких картинок

## Быстрый старт

1. Создай `.env` по примеру [`.env.example`](/Users/abetirov/projects/telegram-content-agent/.env.example)
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
- `ARTICLES_ROOT_PATH` — корневая папка, где сервис ищет статьи в `**/articles/*.md`
- `ARTICLES_AUTO_SYNC_ON_STARTUP` — включить startup-sync статей, по умолчанию `true`

## Правила публикации

- Для штатного сценария используйте `POST /drafts`, а не `POST /publish` или `POST /schedule`.
- `POST /drafts` не поддерживает `dry_run`, а входной `chat_id` игнорирует.
- Локальные статьи из `ARTICLES_ROOT_PATH/**/articles/*.md` автоматически отправляются на модерацию при старте, если у них еще нет успешной отправки.
- Если задан `MODERATION_ALLOWED_USER_IDS`, модерировать могут только эти Telegram user id. Если список пустой, действовать может любой участник `MODERATION_CHAT_ID`.
- После нажатия `Запланировать` время должен прислать отдельным сообщением тот же модератор, который нажал кнопку.
- После нажатия `Отклонить` комментарий тоже должен прийти отдельным сообщением от того же модератора.
- Поддерживаются форматы времени `YYYY-MM-DD HH:MM`, `DD.MM.YYYY HH:MM`, `HH:MM` и ISO 8601.
- Если отправлен только `HH:MM` и это время уже прошло по `MODERATION_TIMEZONE`, публикация переносится на следующий день.
- Сообщение `/cancel` отменяет ожидание времени и возвращает draft в `pending_review`.
- Сообщение `/cancel` в режиме отклонения отменяет ввод комментария и возвращает draft в `pending_review`.
- Комментарии отклонения сохраняются в SQLite и доступны через HTTP API.
- Для автоправки на следующем старте комментарий может содержать инструкции:
  - `title: Новый заголовок`
  - `replace: старый текст => новый текст`
  - `delete: фрагмент`
  - `append: новый абзац`
  - `prepend: вводный абзац`
- Если немедленная публикация в канал завершилась ошибкой, draft не переводится в `failed`: модератор может повторить действие с того же control message.
- Scheduler автоматически ретраит отложенные публикации по настройкам `SCHEDULER_RETRY_DELAY_SECONDS` и `SCHEDULER_MAX_ATTEMPTS`.

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

Если указать только `10:30`, сервис интерпретирует время в `MODERATION_TIMEZONE`, а если оно уже прошло сегодня, перенесет публикацию на завтра.

Отменить режим ввода времени можно сообщением `/cancel`. Время должно прийти отдельным сообщением от того же модератора, который нажал `Запланировать`.

### `GET /drafts`

Список черновиков. Можно фильтровать по `?status=pending_review`.

### `GET /drafts/{id}`

Детали одного moderation draft.

### `GET /drafts/{id}/comments`

Комментарии модератора по конкретному draft-у.

### `GET /articles`

Список статей, которые сервис обнаружил в `ARTICLES_ROOT_PATH`.

### `GET /articles/{id}`

Детали одной статьи и ее текущий lifecycle-статус.

### `GET /articles/{id}/comments`

Комментарии модератора по статье.

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
- длинные plain-text статьи публикуются в Telegram несколькими сообщениями
- если в группе выбрано время, scheduler публикует пост позже автоматически
- scheduler обновляет статус draft-а после фактической публикации
- lifecycle статьи синхронизируется со статусом связанного moderation draft-а
- при отклонении комментарий сохраняется отдельно от draft-а и не теряется между перезапусками
- при ошибке немедленной публикации control message остается активным, чтобы можно было повторить действие
- если картинок нет, используется `sendMessage`
- если картинка одна, используется `sendPhoto`
- если картинок несколько, используется `sendMediaGroup`
- если caption не влезает, сервис отправит картинки и отдельное сообщение с текстом

## Документация

- [Правила публикации](docs/publication-rules.md)
- [Спецификация](docs/spec.md)
- [Сервер и API](docs/server.md)
- [Деплой и GitHub Actions](docs/deployment.md)
- [Публикация из другого чата](docs/publishing-from-chat.md)
