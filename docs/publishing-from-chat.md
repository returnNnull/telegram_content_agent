# Публикация статьи из другого чата

Этот документ нужен для внешнего агента или оператора, который должен отправить статью в уже развернутый сервис.

## Основная схема

1. Агент работает с локальной статьей в репозитории.
2. Агент готовит payload и делает `POST /articles/dry-run`.
3. Если `article_id` раньше отсутствовал, сервер возвращает новый `article_id`.
4. Агент обязан сохранить `article_id` обратно в локальный markdown.
5. После успешного dry run агент вызывает `POST /articles/submit`.
6. Модератор подтверждает публикацию, планирует ее или отклоняет в Telegram.

`POST /publish` и `POST /schedule` остаются только как bypass.

## Где лежат локальные артефакты

- статьи в работе: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/active`
- архив статей: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/archive`
- manifest: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles/index.json`
- обложки: `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/post-assets`
- payload-артефакты: `/Users/abetirov/projects/telegram-content-agent/.codex-local/payloads`

Перед созданием новой темы сначала проверь `index.json` и active-статьи.

## Актуальная схема доступа

- сервер: `155.212.139.15`
- SSH-пользователь: `deploy`
- локальный SSH-ключ: `~/.ssh/github_actions_ci`
- каталог приложения на сервере: `/opt/telegram-content-agent`
- env-файл: `/opt/telegram-content-agent/.env.production`
- dry run endpoint: `http://127.0.0.1:8000/articles/dry-run`
- submit endpoint: `http://127.0.0.1:8000/articles/submit`
- draft inspection: `http://127.0.0.1:8000/drafts`
- article inspection: `http://127.0.0.1:8000/articles`
- bypass publish: `http://127.0.0.1:8000/publish`
- bypass schedule: `http://127.0.0.1:8000/schedule`

## Что нужно подготовить

- локальный markdown статьи с front matter
- payload JSON или эквивалентный payload в памяти
- абсолютные пути к локальным изображениям на сервере, если используются `images[].path`

Минимальные front matter поля:

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

## Быстрая проверка сервиса

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 \
  'curl -sS http://127.0.0.1:8000/health'
```

## Dry run

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -X POST http://127.0.0.1:8000/articles/dry-run \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    --data @- <<'"'"'JSON'"'"'
{
  "article_id": null,
  "title": "Новая статья",
  "slug": "new-article",
  "markdown": "# Новая статья\n\nПодробный текст статьи.\n",
  "cover_path": "publications/InComedy/post-assets/new-article-cover.png",
  "payload_path": ".codex-local/payloads/new-article.json",
  "source_refs": [
    "https://example.com/spec"
  ],
  "attached_links": [
    "https://example.com/pr/1"
  ],
  "payload": {
    "text": "<b>Новая статья</b>\n\nКороткий Telegram-пост.",
    "parse_mode": "HTML",
    "link_style": "buttons"
  }
}
JSON
'
```

Если сервер вернул новый `article.article_id`, агент обязан сохранить его в локальную статью сразу после dry run.

## Submit в moderation flow

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -X POST http://127.0.0.1:8000/articles/submit \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    --data @- <<'"'"'JSON'"'"'
{
  "article_id": "REPLACE_WITH_ARTICLE_ID",
  "title": "Новая статья",
  "slug": "new-article",
  "markdown": "# Новая статья\n\nПодробный текст статьи.\n",
  "cover_path": "publications/InComedy/post-assets/new-article-cover.png",
  "payload_path": ".codex-local/payloads/new-article.json",
  "payload": {
    "text": "<b>Новая статья</b>\n\nКороткий Telegram-пост.",
    "parse_mode": "HTML",
    "link_style": "buttons"
  }
}
JSON
'
```

Ожидаемый результат:

- `article.status = pending_review`
- `draft.status = pending_review`
- в review-группе появляется preview и control message

## Проверка статусов

Статья:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    http://127.0.0.1:8000/articles/REPLACE_WITH_ARTICLE_ID
'
```

Draft attempt:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    http://127.0.0.1:8000/drafts/REPLACE_WITH_DRAFT_ID
'
```

Комментарии модератора:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    http://127.0.0.1:8000/articles/REPLACE_WITH_ARTICLE_ID/comments
'
```

## Как обрабатывать reject

- получить свежий статус статьи через `/articles/{article_id}`
- если статус `rejected`, прочитать комментарии через `/articles/{article_id}/comments`
- сохранить последний комментарий в `moderation_comment` локальной статьи
- отредактировать локальный markdown
- сделать новый `POST /articles/dry-run` с тем же `article_id`
- затем повторить `POST /articles/submit` с тем же `article_id`

Не создавай новую статью поверх старой rejected-статьи.

## Картинки

Если картинки лежат локально, сначала скопируй их на сервер и передавай серверные абсолютные пути в `images[].path`.

## Когда использовать bypass

Использовать `POST /publish` или `POST /schedule` стоит только если:

- moderation flow временно недоступен
- нужен аварийный ручной publish
- выполняется техническая проверка publisher bot

Во всех остальных случаях основной путь: `POST /articles/dry-run` -> сохранить `article_id` -> `POST /articles/submit`.
