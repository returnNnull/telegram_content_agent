# InComedy Content Cycle

Ты работаешь с двумя проектами:

- `/Users/abetirov/AndroidStudioProjects/InComedy`
- `/Users/abetirov/projects/telegram-content-agent`

Сделай один полный цикл article-centric контент-пайплайна по InComedy.

## Режим исполнения

- не пересказывай этот файл в ответе
- следуй ему как runbook
- локальная статья является primary source of truth
- сначала всегда синхронизируй существующие активные статьи с сервером
- новую тему выбирай только если незавершенных неопубликованных статей нет
- не обходи moderation flow без явного основания

## Рабочие каталоги

- активные статьи: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/active`
- архив статей: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/archive`
- manifest: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/index.json`
- обложки: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/post-assets`
- payload-артефакты: `/Users/abetirov/projects/telegram-content-agent/.codex-local/payloads`

Не используй `.codex-local/articles` как рабочее хранилище новой модели.

## Статусы статьи

- `draft`
- `pending_review`
- `awaiting_schedule`
- `scheduled`
- `rejected`
- `published`
- `failed`

Внутренний server-side шаг ожидания комментария к отклонению не является внешним статусом статьи.

## Формат локальной статьи

Каждая локальная статья хранится одним markdown-файлом с YAML front matter.

Минимальные поля:

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

Если `article_id` еще не выдан, он может быть `null`.

## Источники правил

Перед работой прочитай:

- `/Users/abetirov/projects/telegram-content-agent/README.md`
- `/Users/abetirov/projects/telegram-content-agent/docs/server.md`
- `/Users/abetirov/projects/telegram-content-agent/docs/publication-rules.md`
- `/Users/abetirov/projects/telegram-content-agent/docs/publishing-from-chat.md`
- `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/index.json`, если файл существует

Если `index.json` отсутствует, создай его в формате:

```json
{
  "active": [],
  "archive": []
}
```

## Порядок работы

### 1. Синхронизация хвоста

Сначала:

1. Прочитай `index.json`.
2. Загрузи только карточки из `active`.
3. Открой только active-статьи, которые есть в manifest.
4. Для каждой статьи с `article_id` запроси `GET /articles/{article_id}`.
5. Для статей в `rejected` дополнительно запроси `GET /articles/{article_id}/comments`.

Правила:

- если статья на сервере в `pending_review`, `awaiting_schedule` или `scheduled`, обнови локальный front matter и не создавай новую тему
- если статья в `published`, пометь локально как `published`, перенеси файл в `archive`, обнови `index.json`
- если статья в `rejected`, сохрани актуальный комментарий в `moderation_comment`, переработай статью и продолжай работу с ней
- если статья в `failed`, зафиксируй `last_error` и реши, можно ли безопасно продолжить
- если `article_id` у статьи отсутствует, не пытайся синхронизировать ее по серверу; это локальный draft-кандидат

Не загружай весь архив в рабочий контекст.

### 2. Блокировка нового цикла

Если после синхронизации есть хотя бы одна статья в `draft`, `pending_review`, `awaiting_schedule`, `scheduled`, `rejected` или `failed`, сначала двигай именно ее.

Особое правило:

- если статья `rejected`, reject обязан запускать локальную переработку статьи и повторный dry run
- бросать rejected-статью и создавать новую тему запрещено

### 3. Выбор новой темы

Только если активных незавершенных статей нет:

- изучи реальные изменения в `/Users/abetirov/AndroidStudioProjects/InComedy`
- используй `git diff`, связанные коммиты, измененные файлы и `docs/context`
- выбери ровно одну сильную техническую тему
- если сильной темы нет, корректно остановись без новой статьи

### 4. Создание локальной статьи

Для новой статьи:

- создай markdown-файл в `.../articles/active`
- заполни front matter
- `article_id` оставь `null`, если dry run еще не делался
- обнови `index.json`
- создай обложку в `publications/InComedy/post-assets`
- подготовь payload в `.codex-local/payloads`

### 5. Dry run

Перед отправкой обязательно:

- сделай `POST /articles/dry-run`
- если `article_id` отсутствовал, возьми его из ответа и сразу запиши в локальную статью
- обнови `updated_at`, `last_synced_at`, `publish_strategy`, `last_error`
- если single-image single-post разваливается на split, сократи текст и повтори dry run
- если dry run неуспешен, не переходи к submit

Dry run не должен запускать moderation flow.

### 6. Submit

После успешного dry run:

- вызови `POST /articles/submit` с тем же `article_id`
- убедись, что статья получила `pending_review`
- обнови локальный `status`, `updated_at`, `last_synced_at`

### 7. Работа с reject

Если статья отклонена:

1. Прочитай последний комментарий модератора.
2. Сохрани его в `moderation_comment`.
3. Отредактируй локальный markdown.
4. Снова сделай `POST /articles/dry-run` с тем же `article_id`.
5. После успешного dry run снова вызови `POST /articles/submit` с тем же `article_id`.

Не создавай новый `slug` и не создавай новую статью поверх той же темы.

### 8. Работа с publish

Если статья стала `published`:

- обнови локальный `status`
- перенеси файл из `active` в `archive`
- обнови `index.json`
- archive не загружай в рабочий контекст целиком

## Минимальные требования к материалу

- тема основана на реальных diff, коде и документах
- статья и пост не выдумывают факты
- есть сильный инженерный вывод
- приложены ключевые ссылки
- dry run пройден
- отправка сделана только через article-centric flow

## Запрещено

- считать сервер владельцем локального markdown
- использовать статус-по-папке как главный механизм workflow
- создавать новую тему при наличии незавершенной active-статьи
- использовать `POST /drafts` как основной путь
- обходить reject без локальной переработки статьи
- переиспользовать legacy `prepared-tail` как отдельную полусущность вне article lifecycle

## Короткий финальный отчет

В конце дай короткий отчет:

- какая статья была в работе
- был ли sync с сервером
- был ли dry run
- был ли получен или сохранен `article_id`
- была ли отправка в moderation flow
- какой текущий статус статьи
- была ли архивирована статья
