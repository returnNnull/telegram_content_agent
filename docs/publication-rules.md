# Правила публикации

## Роли

- локальный агент владеет editorial lifecycle статьи
- локальная markdown-статья с YAML front matter является source of truth
- сервер хранит publishable snapshot статьи в БД и проводит moderation/schedule/publish flow
- модератор принимает решение в review-группе Telegram

## Локальная модель статьи

Статья хранится в одном markdown-файле с front matter. Минимальные поля:

- `article_id`
- `status`
- `created_at`
- `updated_at`
- `scheduled_publish_at`
- `moderation_comment`
- `title`
- `slug`
- `cover_path`
- `payload_path`

Допустимые дополнительные поля:

- `source_refs`
- `attached_links`
- `last_synced_at`
- `publish_strategy`
- `last_error`

`article_id` может быть `null` до первого успешного dry run.

## Локальная структура

- `publications/InComedy/articles/active`
- `publications/InComedy/articles/archive`
- `publications/InComedy/articles/index.json`
- `publications/InComedy/post-assets`
- `.codex-local/payloads`

Правила:

- workflow не раскладывается по папкам `draft/rejected/scheduled`
- статус живет в front matter
- `active` содержит только незавершенные статьи
- `archive` содержит `published` и явный архив
- агент читает сначала `index.json`, а не сканирует весь архив

## Штатный flow агента

1. Прочитать `publications/InComedy/articles/index.json`.
2. Загрузить только active-статьи.
3. Найти все неопубликованные статьи.
4. Синхронизировать их с сервером по `article_id`, если он уже есть.
5. Если есть статья в `pending_review`, `awaiting_schedule`, `scheduled`, `rejected` или `failed`, агент работает с ней, а не создает новую.
6. Только если незавершенных статей нет, агент выбирает новую тему.

## Правила dry run

- агент всегда делает dry run до moderation flow
- основной endpoint: `POST /articles/dry-run`
- dry run не должен запускать moderation flow
- dry run без `article_id` создает server-side article record и возвращает новый `article_id`
- агент обязан сразу записать этот `article_id` обратно в локальную статью
- dry run с существующим `article_id` обновляет snapshot той же статьи
- если single-image single-post разваливается на несколько сообщений, агент обязан сократить текст и повторить dry run

## Правила submit

- после успешного dry run агент вызывает `POST /articles/submit`
- primary identity в submit flow: `article_id`
- сервер создает moderation draft, но draft не заменяет article identity
- `POST /drafts` не является основным путем агента

## Правила синхронизации статусов

Если на сервере статья:

- `pending_review` — обновить локальный статус и не создавать новую тему
- `awaiting_schedule` — обновить локальный статус и ждать решения модератора
- `scheduled` — обновить `scheduled_publish_at`
- `rejected` — сохранить `moderation_comment`, переработать локальную статью, сделать новый dry run и повторный submit
- `published` — пометить локально как `published`, перенести в `archive`, обновить `index.json`
- `failed` — зафиксировать ошибку локально и решить, можно ли безопасно повторить цикл

Reject не является terminal outcome для агента.

## Правила модерации

- решения принимаются только в `MODERATION_CHAT_ID`
- если задан `MODERATION_ALLOWED_USER_IDS`, только они могут модерировать
- кнопка `Запланировать` переводит draft в ожидание времени
- время должен прислать тот же модератор отдельным сообщением
- кнопка `Отклонить` запускает ввод комментария отдельным сообщением
- комментарий сохраняется в БД и копируется в `moderation_comment` статьи

## Статусы статьи

- `draft`
- `pending_review`
- `awaiting_schedule`
- `scheduled`
- `rejected`
- `published`
- `failed`

Внутренний шаг ожидания rejection comment не должен ломать внешнюю article-centric модель.

## Правила архивации

- опубликованная статья переносится из `active` в `archive`
- `index.json` обновляется консистентно при создании, изменении, синхронизации и архивации статьи
- archive не должен загружаться целиком в рабочий контекст агента
