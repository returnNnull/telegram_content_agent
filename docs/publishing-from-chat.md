# Публикация постов из другого чата

## Назначение

Этот документ нужен для другого чата или агента, который должен отправить статью в текущий сервис, не разбираясь в устройстве кода.

Теперь основной flow такой:

1. агент готовит статью
2. агент вызывает `POST /drafts`
3. moderation bot отправляет статью в review-группу
4. человек в review-группе подтверждает публикацию или задает время
5. publisher bot публикует статью в основной канал

`POST /publish` и `POST /schedule` оставлены только как ручной bypass и не являются штатным сценарием.

## Актуальная схема

- сервер: `155.212.139.15`
- SSH-пользователь: `deploy`
- локальный SSH-ключ для доступа к серверу: `~/.ssh/github_actions_ci`
- каталог приложения на сервере: `/opt/telegram-content-agent`
- env-файл на сервере: `/opt/telegram-content-agent/.env.production`
- endpoint отправки draft-а: `http://127.0.0.1:8000/drafts`
- bypass publish endpoint: `http://127.0.0.1:8000/publish`
- bypass schedule endpoint: `http://127.0.0.1:8000/schedule`
- health-check на сервере: `http://127.0.0.1:8000/health`

`PUBLISH_API_TOKEN` не хранится в репозитории. Его нужно читать на сервере из `.env.production` прямо перед вызовом API.

## Базовый сценарий

1. Просмотреть последний релевантный коммит в проекте `/Users/abetirov/AndroidStudioProjects/InComedy`.
2. Свериться с предыдущими постами в `/Users/abetirov/AndroidStudioProjects/InComedy/.codex-local/post-drafts.md`.
3. Подготовить текст статьи, ссылки и список картинок.
4. Если картинки локальные, сначала скопировать их на сервер.
5. Подключиться к серверу по SSH или выполнить удаленную команду.
6. Прочитать `PUBLISH_API_TOKEN` из `/opt/telegram-content-agent/.env.production`.
7. Вызвать `POST /drafts`.
8. Дальнейшее решение о публикации принимается человеком в review-группе Telegram.

## Правила подготовки текста

- по умолчанию основой поста служит последний коммит или явно указанное изменение в `/Users/abetirov/AndroidStudioProjects/InComedy`
- нельзя писать пост только по commit message: нужно смотреть diff и ключевые измененные файлы
- перед написанием поста нужно изучать связанную документацию проекта в `/Users/abetirov/AndroidStudioProjects/InComedy/docs/context`
- перед новым черновиком нужно сверяться с предыдущими постами в `/Users/abetirov/AndroidStudioProjects/InComedy/.codex-local/post-drafts.md`
- в пост нужно выносить только значимые решения: продуктовые, архитектурные и инженерные правила
- к посту по умолчанию нужно прикладывать ссылки на ключевые документы, решения или спецификации
- если пост с одной картинкой, текст желательно ужимать так, чтобы он помещался в caption

## Быстрая проверка сервиса

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 \
  'curl -sS http://127.0.0.1:8000/health'
```

Ожидаемый ответ содержит:

- `status`
- `channel_id`
- `moderation_chat_id`

## Отправка draft-а на модерацию

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS -X POST http://127.0.0.1:8000/drafts \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    --data @- <<'"'"'JSON'"'"'
{
  "text": "<b>Новая статья</b>\n\nЭто черновик для review-группы.",
  "parse_mode": "HTML",
  "link_style": "buttons",
  "links": [
    {
      "title": "Спецификация",
      "url": "https://github.com/returnNnull/telegram_content_agent/blob/main/docs/server.md"
    }
  ]
}
JSON
'
```

Ответ вернет:

- `id` draft-а
- `status = pending_review`
- сохраненный payload

После этого статья появится в review-группе, а рядом будет control message с кнопками:

- `Опубликовать сейчас`
- `Запланировать`
- `Отклонить`

## Как задать время публикации

Время задается не через API, а в самой review-группе:

1. нажать `Запланировать`
2. отправить время отдельным сообщением

Поддерживаются форматы:

- `2026-03-23 10:30`
- `23.03.2026 10:30`
- `10:30`
- `2026-03-23T10:30:00+03:00`

Если timezone не указана, используется `MODERATION_TIMEZONE`.
Сообщение `/cancel` отменяет режим ввода времени.

## Проверка draft-ов через API

Список draft-ов:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS \
    -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    "http://127.0.0.1:8000/drafts?status=pending_review"
'
```

Детали одного draft-а:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 '
  set -a
  source /opt/telegram-content-agent/.env.production
  curl -sS \
    -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
    http://127.0.0.1:8000/drafts/REPLACE_WITH_ID
'
```

## Публикация с одной или несколькими картинками

### Если картинки уже доступны по URL

Их можно передавать напрямую в `images[].url`.

```json
{
  "text": "Пост с удаленной картинкой",
  "images": [
    {
      "url": "https://example.com/image.png"
    }
  ]
}
```

### Если картинки лежат локально на твоей машине

Сначала скопируй их на сервер:

```bash
ssh -i ~/.ssh/github_actions_ci deploy@155.212.139.15 \
  'mkdir -p /opt/telegram-content-agent/channel-assets'

scp -i ~/.ssh/github_actions_ci \
  /absolute/path/to/image1.png \
  /absolute/path/to/image2.png \
  deploy@155.212.139.15:/opt/telegram-content-agent/channel-assets/
```

Потом укажи серверные абсолютные пути в `POST /drafts`:

```json
{
  "text": "<b>Пост с картинками</b>\n\nТекст статьи.",
  "parse_mode": "HTML",
  "images": [
    {
      "path": "/opt/telegram-content-agent/channel-assets/image1.png"
    },
    {
      "path": "/opt/telegram-content-agent/channel-assets/image2.png"
    }
  ]
}
```

## Когда использовать bypass endpoint'ы

Использовать `POST /publish` или `POST /schedule` стоит только если:

- moderation flow временно недоступен
- нужен ручной форсированный publish без review-группы
- выполняется техническая проверка канального бота

Во всех остальных случаях основной endpoint — `POST /drafts`.
