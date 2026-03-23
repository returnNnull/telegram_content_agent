# telegram-content-agent

Сервис для article-centric публикации статей в Telegram через review-группу и два Telegram-бота в одном FastAPI-процессе.

## Актуальная модель

- Локальная статья всегда является primary source of truth.
- Локальная статья хранится как один markdown-файл с YAML front matter.
- До первого успешного dry run `article_id` может отсутствовать.
- `POST /articles/dry-run` создает или обновляет server-side snapshot статьи и, если нужно, выдает новый `article_id`.
- Агент обязан сразу сохранить выданный `article_id` обратно в локальный markdown.
- `POST /articles/submit` отправляет в moderation flow тот же `article_id`.
- Сервер хранит snapshot статьи и payload в SQLite и публикует подтвержденный или запланированный материал из БД, без повторного чтения локального markdown.

Сервер больше не сканирует `publications/**/articles/*.md` на старте и не инициирует lifecycle локальных статей сам.

## Локальная структура

```text
publications/InComedy/articles/active
publications/InComedy/articles/archive
publications/InComedy/articles/index.json
publications/InComedy/post-assets
.codex-local/payloads
```

Правила:

- в `active` лежат только статьи в работе со статусами `draft`, `pending_review`, `awaiting_schedule`, `scheduled`, `rejected`, `failed`
- в `archive` лежат `published` и явно архивированные материалы
- workflow-статус живет в YAML front matter, а не в названии папки
- перемещение между `active` и `archive` делается только при архивировании
- агент читает сначала `index.json`, затем только active-статьи

## Формат локальной статьи

Минимальный front matter:

```yaml
---
article_id: null
status: "draft"
created_at: "2026-03-23T09:00:00+00:00"
updated_at: "2026-03-23T09:00:00+00:00"
scheduled_publish_at: null
moderation_comment: null
title: "Почему ticketing появился раньше выбора провайдера"
slug: "ticketing-before-provider-choice"
cover_path: "publications/InComedy/post-assets/ticketing_before_provider_cover.png"
payload_path: ".codex-local/payloads/ticketing-before-provider-choice.json"
source_refs:
  - "https://example.com/spec"
attached_links:
  - "https://example.com/pr/1"
last_synced_at: null
publish_strategy: null
last_error: null
---
```

`cover_path` и `payload_path` являются ссылками на артефакты, а не источником истины по статье.

## Статусы статьи

- `draft`
- `pending_review`
- `awaiting_schedule`
- `scheduled`
- `rejected`
- `published`
- `failed`

Внутренний технический статус ожидания комментария к отклонению остается только внутри moderation flow и не является внешним article-status.

## API

### Основной flow

#### `POST /articles/dry-run`

Primary preflight endpoint.

- если `article_id` отсутствует, сервер создает новый article record и возвращает `article_id`
- если `article_id` уже есть, сервер обновляет snapshot той же статьи
- moderation flow не запускается
- в БД сохраняются markdown snapshot, payload snapshot и метаданные статьи

```bash
curl -X POST http://localhost:8000/articles/dry-run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer replace_with_long_random_token" \
  -d '{
    "title": "Новая статья",
    "slug": "new-article",
    "markdown": "# Новая статья\n\nТекст статьи.\n",
    "cover_path": "publications/InComedy/post-assets/new-article-cover.png",
    "payload_path": ".codex-local/payloads/new-article.json",
    "payload": {
      "text": "<b>Новая статья</b>\n\nКороткий Telegram-пост.",
      "parse_mode": "HTML",
      "link_style": "buttons"
    }
  }'
```

#### `POST /articles/submit`

Штатная отправка статьи в moderation flow.

- принимает тот же article snapshot
- использует существующий `article_id` или создаст его при совместимом fallback-сценарии
- создает moderation draft, связанный с `article_id`
- отправляет preview в review-группу и control message

#### `GET /articles`

Список server-side article records. Это article-centric snapshot-слой в SQLite, а не сканирование локального диска.

#### `GET /articles/{article_id}`

Детали статьи по стабильному `article_id`.

#### `GET /articles/{article_id}/comments`

Комментарии модератора по статье.

### Вспомогательные endpoints

- `GET /drafts`
- `GET /drafts/{draft_id}`
- `GET /drafts/{draft_id}/comments`

`draft` теперь является попыткой модерации, а не primary identity статьи.

### Bypass endpoints

- `POST /publish` — прямой publish и dry run для технических сценариев
- `POST /schedule` — прямое создание scheduled post
- `GET /schedule`
- `GET /schedule/{id}`
- `DELETE /schedule/{id}`

## Поведение сервиса

- moderation flow использует два разных bot token в одном процессе
- preview в review-группе уходит через moderation bot
- публикация в канал уходит через publisher bot
- scheduler обновляет статус article record после фактической публикации
- reject сохраняет `moderation_comment` и переводит статью в `rejected`
- confirm публикует snapshot статьи из БД и переводит статью в `published`
- schedule сохраняет `scheduled_publish_at`, публикует позже из БД и переживает рестарт

## Переменные окружения

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `MODERATION_BOT_TOKEN`
- `MODERATION_CHAT_ID`
- `MODERATION_TIMEZONE`
- `MODERATION_ALLOWED_USER_IDS`
- `MODERATION_POLL_INTERVAL_SECONDS`
- `MODERATION_POLL_TIMEOUT_SECONDS`
- `PUBLISH_API_TOKEN`
- `TELEGRAM_API_BASE`
- `REQUEST_TIMEOUT_SECONDS`
- `DEFAULT_PARSE_MODE`
- `DEFAULT_LINK_STYLE`
- `SCHEDULER_DB_PATH`
- `SCHEDULER_POLL_INTERVAL_SECONDS`
- `SCHEDULER_BATCH_SIZE`
- `SCHEDULER_RETRY_DELAY_SECONDS`
- `SCHEDULER_MAX_ATTEMPTS`

`ARTICLES_ROOT_PATH` и `ARTICLES_AUTO_SYNC_ON_STARTUP` больше не используются.

## Документация

- [Правила публикации](docs/publication-rules.md)
- [Сервер и API](docs/server.md)
- [Публикация из другого чата](docs/publishing-from-chat.md)
- [Деплой и GitHub Actions](docs/deployment.md)
- [Спецификация](docs/spec.md)
