# Сервер публикации в Telegram

## Назначение

Сервис принимает article-centric snapshot статьи, проводит ее через moderation flow и публикует подтвержденный snapshot в Telegram.

В системе есть две главные сущности:

- `article` — стабильная article-centric запись с `article_id`, статусом, markdown snapshot и publishable payload snapshot
- `draft` — отдельная попытка модерации для статьи

`article_id` является primary id. `draft_id` нужен только для внутренних попыток модерации и control message.

## Что сервер хранит в БД

Минимально сервер хранит:

- `article_id`
- `status`
- `created_at`
- `updated_at`
- `markdown` snapshot статьи
- `payload` snapshot, достаточный для реальной публикации
- `moderation_comment`
- `scheduled_publish_at`
- `published_at`
- `last_error`
- `title`
- `slug`
- `cover_path`
- `payload_path`
- `source_refs`
- `attached_links`
- `publish_strategy`
- `current_draft_id`

Это позволяет:

- делать safe dry run
- отправлять статью на модерацию
- публиковать подтвержденную статью после delay или schedule
- восстанавливаться после рестарта

Сервер не зависит от повторного чтения локального markdown-файла для фактической публикации.

## Чего сервер больше не делает

- не сканирует `publications/**/articles/*.md` на старте
- не использует `source_path` как identity статьи
- не инициирует lifecycle локальных статей сам
- не является владельцем локального article file

## Основной API

### `POST /articles/dry-run`

Primary preflight endpoint.

Контракт:

- если `article_id` отсутствует, сервер создает новую article record и возвращает новый `article_id`
- если `article_id` уже есть, обновляет snapshot этой статьи
- moderation flow не запускается
- статус статьи после dry run остается `draft`

Пример запроса:

```json
{
  "article_id": null,
  "title": "Новая статья",
  "slug": "new-article",
  "markdown": "# Новая статья\n\nТекст статьи.\n",
  "cover_path": "publications/InComedy/post-assets/new-article-cover.png",
  "payload_path": ".codex-local/payloads/new-article.json",
  "source_refs": [
    "https://example.com/spec"
  ],
  "attached_links": [
    "https://example.com/pr/1"
  ],
  "payload": {
    "text": "<b>Новая статья</b>\n\nTelegram preview",
    "parse_mode": "HTML",
    "link_style": "buttons"
  }
}
```

Пример ответа:

```json
{
  "article": {
    "article_id": "d1a9d80a53e9423fad1fd43a1f5a4d2d",
    "status": "draft",
    "title": "Новая статья",
    "slug": "new-article",
    "publish_strategy": "message"
  },
  "dry_run": {
    "ok": true,
    "strategy": "message"
  }
}
```

### `POST /articles/submit`

Штатная отправка статьи в moderation flow.

Контракт:

- агент должен сначала пройти `POST /articles/dry-run`
- затем отправить в `POST /articles/submit` тот же `article_id`
- сервер сохраняет или обновляет snapshot статьи в БД
- сервер создает moderation draft, связанный с `article_id`
- moderation bot отправляет preview и control message

### `GET /articles`

Список server-side article records. Это manifest server-side snapshot-ов, а не список файлов на диске.

### `GET /articles/{article_id}`

Детали статьи по стабильному `article_id`.

### `GET /articles/{article_id}/comments`

История комментариев модератора для статьи.

## Draft API

- `GET /drafts`
- `GET /drafts/{draft_id}`
- `GET /drafts/{draft_id}/comments`

Draft API полезен для наблюдения за попытками модерации, но primary workflow идет через `/articles/*`.

`POST /drafts` оставлен только как совместимый/manual endpoint для raw moderation draft без article-centric local workflow. Он не должен считаться основным путем для агента.

## Модель статусов

Внешние статусы статьи:

- `draft`
- `pending_review`
- `awaiting_schedule`
- `scheduled`
- `rejected`
- `published`
- `failed`

Внутренний moderation-step `awaiting_rejection_comment` остается только внутри draft flow и не публикуется как основной article status.

## Как работает moderation flow

### Подтвердить публикацию

- модератор нажимает `Опубликовать сейчас`
- сервер берет payload snapshot статьи из БД
- publisher bot публикует его в канал
- статья переходит в `published`

### Запланировать

- модератор нажимает `Запланировать`
- draft переходит в `awaiting_schedule`
- тот же модератор присылает время отдельным сообщением
- сервер создает scheduled post и переводит статью в `scheduled`
- в нужный момент scheduler публикует snapshot из БД

### Отклонить

- модератор нажимает `Отклонить`
- draft внутренне ждет комментарий
- комментарий сохраняется в БД
- статья получает `moderation_comment` и статус `rejected`

## Scheduler и рестарт

- scheduler хранит due-задачи в SQLite
- при рестарте `processing` задачи возвращаются в `pending`
- статья и payload snapshot не теряются между рестартами
- scheduled publish не требует локального markdown-файла

## Bypass API

### `POST /publish`

Прямой publish и dry run через publisher bot. Это технический и аварийный endpoint, а не штатный article flow.

### `POST /schedule`

Прямое создание scheduled post через scheduler.

## Legacy и совместимость

- старая таблица `articles`, если она есть в SQLite, считается legacy
- новая рабочая article-centric модель живет в `article_records`
- при инициализации выполняется best-effort backfill старых статей в новую таблицу
- старые env `ARTICLES_ROOT_PATH` и `ARTICLES_AUTO_SYNC_ON_STARTUP` больше не используются
