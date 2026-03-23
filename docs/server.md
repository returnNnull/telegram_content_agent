# Сервер публикации в Telegram

## Назначение

Сервис принимает готовую статью и проводит ее через moderation flow с двумя Telegram-ботами:

1. moderation bot отправляет статью в review-группу
2. вы в группе решаете, публиковать сразу или отложить
3. publisher bot публикует статью в основной канал

Прямые `POST /publish` и `POST /schedule` оставлены как bypass и не являются основным сценарием.

## Компоненты

- `publisher bot` — токен из `TELEGRAM_BOT_TOKEN`, публикует в `TELEGRAM_CHANNEL_ID`
- `moderation bot` — токен из `MODERATION_BOT_TOKEN`, работает в `MODERATION_CHAT_ID`
- `scheduler` — хранит и исполняет отложенные публикации
- `SQLite` — хранит moderation drafts и scheduled posts в одном файле из `SCHEDULER_DB_PATH`

## Основной контракт

### `POST /drafts`

Требует заголовок:

```text
Authorization: Bearer <PUBLISH_API_TOKEN>
```

Payload совпадает с обычным publish-запросом, кроме того что `dry_run` не поддерживается, а `chat_id` игнорируется.

```json
{
  "text": "<b>Заголовок</b>\n\nТекст статьи",
  "parse_mode": "HTML",
  "images": [
    {
      "url": "https://example.com/image.png"
    }
  ],
  "links": [
    {
      "title": "PR",
      "url": "https://github.com/org/repo/pull/123"
    }
  ],
  "link_style": "buttons",
  "disable_web_page_preview": false,
  "disable_notification": false,
  "protect_content": false
}
```

Что делает endpoint:

1. сохраняет moderation draft в SQLite
2. отправляет статью в review-группу через moderation bot
3. публикует control message с inline-кнопками:
   - `Опубликовать сейчас`
   - `Запланировать`
   - `Отклонить`

Пример ответа:

```json
{
  "id": "0d8c3cb6c3a046e3b7f68b99c0ebca8f",
  "status": "pending_review",
  "created_at": "2026-03-22T09:15:10.000000Z",
  "updated_at": "2026-03-22T09:15:10.000000Z",
  "published_at": null,
  "rejected_at": null,
  "scheduled_publish_at": null,
  "scheduled_post_id": null,
  "last_error": null,
  "request": {
    "text": "<b>Заголовок</b>\n\nТекст статьи",
    "parse_mode": "HTML",
    "images": [],
    "links": [],
    "link_style": "buttons",
    "disable_web_page_preview": false,
    "disable_notification": false,
    "protect_content": false,
    "dry_run": false,
    "chat_id": null
  },
  "publication_result": null
}
```

## Как работает модерация в чате

### Опубликовать сразу

По кнопке `Опубликовать сейчас` сервис:

- берет сохраненный payload draft-а
- публикует его в основной канал через publisher bot
- переводит draft в статус `published`

### Отложить публикацию

По кнопке `Запланировать` moderation bot просит прислать время отдельным сообщением.

Поддерживаемые форматы:

- `YYYY-MM-DD HH:MM`
- `DD.MM.YYYY HH:MM`
- `HH:MM`
- ISO 8601, например `2026-03-23T10:30:00+03:00`

Если timezone в сообщении не указана, используется `MODERATION_TIMEZONE`.
Сообщение `/cancel` отменяет режим ввода времени и возвращает draft в `pending_review`.

После корректного времени сервис:

- создает задачу в scheduler
- переводит draft в статус `scheduled`
- после фактической отправки синхронизирует его в `published`

### Ограничение по пользователям

Если задан `MODERATION_ALLOWED_USER_IDS`, только эти Telegram user id могут нажимать кнопки и задавать время. Если список пустой, действовать может любой участник `MODERATION_CHAT_ID`.

## Draft endpoints

- `GET /drafts` — список draft-ов, можно фильтровать по `?status=scheduled`
- `GET /drafts/{id}` — детали одного draft-а

Статусы draft-а:

- `pending_review` — ждет решения в review-группе
- `awaiting_schedule` — сервис ждет время публикации сообщением
- `scheduled` — публикация создана в scheduler
- `published` — статья ушла в основной канал
- `rejected` — черновик отклонен
- `failed` — не удалось доставить draft в review-группу или завершить связанный scheduled flow

## Bypass endpoints

### `POST /publish`

Прямой publish в канал через publisher bot.

### `POST /schedule`

Прямая отложенная публикация в канал. Формат payload тот же, что у `POST /publish`, плюс обязательное `publish_at`.

```json
{
  "text": "<b>Заголовок</b>\n\nТекст поста",
  "publish_at": "2026-03-23T10:30:00+03:00"
}
```

### Дополнительные endpoints scheduler

- `GET /schedule`
- `GET /schedule/{id}`
- `DELETE /schedule/{id}`

## Как сервер выбирает стратегию публикации контента

- `0` картинок: `sendMessage`
- `1` картинка: `sendPhoto`
- `2-10` картинок: `sendMediaGroup`

Если caption не помещается:

- сначала уходят картинки
- потом отдельным сообщением уходит текст

Если `link_style = "buttons"`:

- ссылки отправляются как inline-кнопки

Если `link_style = "text"`:

- ссылки добавляются в конец текста поста

## Ограничения

- поддержан только `HTML` parse mode или plain text
- `sendMediaGroup` не умеет inline-кнопки, поэтому кнопки со ссылками уходят отдельным сообщением
- для локальных изображений нужно передавать абсолютный путь
- за один запрос допускается до `10` картинок
- `publish_at` должен быть в будущем и обязательно содержать timezone
- moderation bot использует long polling через `getUpdates`, поэтому серверу нужен исходящий доступ к Telegram API
