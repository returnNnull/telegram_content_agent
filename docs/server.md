# Сервер публикации в Telegram

## Назначение

Сервис принимает готовый payload поста и проводит его через moderation flow с двумя Telegram-ботами:

1. moderation bot отправляет статью в review-группу
2. вы в группе решаете, публиковать сразу или отложить
3. publisher bot публикует статью в основной канал

Прямые `POST /publish` и `POST /schedule` оставлены как bypass и не являются основным сценарием.

Дополнительно при старте сервис сканирует `ARTICLES_ROOT_PATH/**/articles/*.md`, регистрирует найденные статьи в SQLite и автоматически отправляет на модерацию те, у которых еще нет активной отправки.

## Компоненты

- `publisher bot` — токен из `TELEGRAM_BOT_TOKEN`, публикует в `TELEGRAM_CHANNEL_ID`
- `moderation bot` — токен из `MODERATION_BOT_TOKEN`, работает в `MODERATION_CHAT_ID`
- `scheduler` — хранит и исполняет отложенные публикации
- `SQLite` — хранит moderation drafts, статьи, комментарии модератора и scheduled posts в одном файле из `SCHEDULER_DB_PATH`

## Основные правила

- Для штатной публикации используйте `POST /drafts`.
- `POST /drafts` не принимает `dry_run`, а поле `chat_id` игнорирует.
- Финальный publish из moderation flow всегда идет в `TELEGRAM_CHANNEL_ID`.
- Решения по draft-ам принимаются только в `MODERATION_CHAT_ID`.
- Если задан `MODERATION_ALLOWED_USER_IDS`, кнопки, ввод времени и ввод комментария доступны только этим пользователям.
- Локальные markdown-статьи из `ARTICLES_ROOT_PATH` проходят тот же moderation flow, что и обычные draft-ы.

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

Если draft был создан из локальной статьи, ответ также содержит:

- `article_id`
- `article_attempt`
- `article_source_hash`

Если preview или control message не удалось доставить в review-группу, endpoint вернет `502`, а draft будет сохранен в статусе `failed`.

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
    "images": [
      {
        "url": "https://example.com/image.png",
        "path": null
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

Если publish в канал падает, draft не переводится в `failed`. Сервис показывает ошибку модератору через callback, а control message остается активным для повторной попытки.

### Отложить публикацию

По кнопке `Запланировать` moderation bot просит прислать время отдельным сообщением.

Важное правило: время должен прислать тот же модератор, который нажал `Запланировать`. Состояние ожидания времени хранится в связке `chat_id + user_id`.

Поддерживаемые форматы:

- `YYYY-MM-DD HH:MM`
- `DD.MM.YYYY HH:MM`
- `HH:MM`
- ISO 8601, например `2026-03-23T10:30:00+03:00`

Если timezone в сообщении не указана, используется `MODERATION_TIMEZONE`.
Если прислать только `HH:MM` и это время уже прошло сегодня, сервис перенесет публикацию на следующий день в `MODERATION_TIMEZONE`.
Сообщение `/cancel` отменяет режим ввода времени и возвращает draft в `pending_review`.

После корректного времени сервис:

- создает задачу в scheduler
- переводит draft в статус `scheduled`
- после фактической отправки синхронизирует его в `published`
- если scheduler исчерпал попытки отправки, переводит draft в `failed`

### Отклонить с комментарием

По кнопке `Отклонить` moderation bot переводит draft в `awaiting_rejection_comment` и просит прислать комментарий отдельным сообщением.

Важные правила:

- комментарий должен прислать тот же модератор, который нажал `Отклонить`
- сообщение `/cancel` отменяет режим ввода комментария и возвращает draft в `pending_review`
- комментарий сохраняется в SQLite и доступен через HTTP API

Если комментарий написан в формате edit-инструкций, сервис может автоматически применить его к markdown-статье на следующем старте и повторно отправить статью на модерацию. Поддерживаются инструкции:

- `title: Новый заголовок`
- `replace: старый текст => новый текст`
- `delete: фрагмент`
- `append: новый абзац`
- `prepend: вводный абзац`

### Ограничение по пользователям

Если задан `MODERATION_ALLOWED_USER_IDS`, только эти Telegram user id могут нажимать кнопки и задавать время. Если список пустой, действовать может любой участник `MODERATION_CHAT_ID`.

## Draft endpoints

- `GET /drafts` — список draft-ов, можно фильтровать по `?status=scheduled`
- `GET /drafts/{id}` — детали одного draft-а
- `GET /drafts/{id}/comments` — комментарии модератора по конкретному draft-у

## Article endpoints

- `GET /articles` — список локальных статей, обнаруженных в `ARTICLES_ROOT_PATH`
- `GET /articles/{id}` — детали статьи и ее lifecycle-статус
- `GET /articles/{id}/comments` — комментарии модератора по статье

Статусы draft-а:

- `pending_review` — ждет решения в review-группе
- `awaiting_schedule` — сервис ждет время публикации сообщением
- `awaiting_rejection_comment` — сервис ждет комментарий к отклонению отдельным сообщением
- `scheduled` — публикация создана в scheduler
- `published` — статья ушла в основной канал
- `rejected` — черновик отклонен
- `failed` — не удалось доставить draft в review-группу или завершить связанный scheduled flow

## Bypass endpoints

### `POST /publish`

Прямой publish через publisher bot.

- По умолчанию уходит в `TELEGRAM_CHANNEL_ID`.
- Если передан `chat_id`, publish уйдет в него, а не в default channel.
- Endpoint поддерживает `dry_run`.

### `POST /schedule`

Прямая отложенная публикация через scheduler. Формат payload тот же, что у `POST /publish`, плюс обязательное `publish_at`.

- По умолчанию scheduler потом отправит пост в `TELEGRAM_CHANNEL_ID`.
- Если передан `chat_id`, scheduled post будет опубликован именно туда.
- `publish_at` обязан быть в будущем и обязательно содержать timezone.
- `dry_run` не поддерживается.

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

`DELETE /schedule/{id}` доступен только для задач в статусе `pending` или `failed`.

Если scheduled post был создан из moderation flow, отмена через `DELETE /schedule/{id}` не переведет связанный moderation draft обратно из `scheduled`. Это текущая особенность реализации.

## Как сервер выбирает стратегию публикации контента

- `0` картинок: `sendMessage`
- `1` картинка: `sendPhoto`
- `2-10` картинок: `sendMediaGroup`
- длинный plain-text без HTML сервис разбивает на несколько `sendMessage`, чтобы пройти лимит Telegram

Если caption не помещается:

- сначала уходят картинки
- потом отдельным сообщением уходит текст

Если `link_style = "buttons"`:

- ссылки отправляются как inline-кнопки

Если `link_style = "text"`:

- ссылки добавляются в конец текста поста
- при длине текста оставляйте запас под URL, потому что ссылки дописываются уже после валидации поля `text`

## Ограничения

- запрос должен содержать хотя бы один из `text`, `images`, `links`
- поддержан только `HTML` parse mode или plain text
- `sendMediaGroup` не умеет inline-кнопки, поэтому кнопки со ссылками уходят отдельным сообщением
- для локальных изображений нужно передавать абсолютный путь
- за один запрос допускается до `10` картинок
- за один запрос допускается до `10` ссылок
- заголовок ссылки ограничен `64` символами
- moderation bot использует long polling через `getUpdates`, поэтому серверу нужен исходящий доступ к Telegram API
- scheduler ретраит ошибки по `SCHEDULER_RETRY_DELAY_SECONDS` и `SCHEDULER_MAX_ATTEMPTS`
