# InComedy Content Cycle

Ты работаешь с двумя проектами:

- `/Users/abetirov/AndroidStudioProjects/InComedy`
- `/Users/abetirov/projects/telegram-content-agent`

Сделай один полный цикл контент-пайплайна по InComedy.

## Цель

- найти одну сильную техническую тему по реальным изменениям InComedy;
- подготовить статью, Telegram-пост и обложку;
- проверить материал;
- сделать dry_run;
- при успехе отправить draft в moderation flow через Telegram content agent;
- обновить локальный учет;
- не делать новую отправку, если прошлый хвост не разобран.

Используй только реальные данные из diff, кода, `docs/context` и официальных источников. Ничего не выдумывай. Если тема слабая или материал сырой, пропусти цикл.

## Критические правила

- штатный путь отправки: `POST /drafts`;
- `dry_run` разрешен только через `POST /publish` с `dry_run=true`;
- `POST /publish` без `dry_run` и `POST /schedule` используй только как bypass, если moderation flow недоступен, есть явное указание обойти его или нужна аварийная публикация;
- отправка draft не равна публикации;
- сервисный lifecycle статьи через `/articles` используй для наблюдения и синхронизации статусов, но не считай его заменой `POST /drafts` как trigger штатной отправки;
- ориентируйся на текущий moderation chat из конфигурации сервиса;
- новые статьи и новые обложки сохраняй только в:
  - `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles`
  - `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/post-assets`
- legacy-каталоги используй только как архив и источник стиля:
  - `/Users/abetirov/projects/telegram-content-agent/.codex-local/articles`
  - `/Users/abetirov/projects/telegram-content-agent/.codex-local/post-assets`
- не создавай новую отправку при любом блокирующем условии;
- не обходи moderation flow без явного основания;
- не считай отправку draft публикацией;
- не записывай новые статьи или новые обложки в legacy-каталоги.

## Файлы состояния

- `/Users/abetirov/projects/telegram-content-agent/.codex-local/post-drafts.md`
- `/Users/abetirov/projects/telegram-content-agent/.codex-local/publication-log.json`
- `/Users/abetirov/projects/telegram-content-agent/.codex-local/payloads`
- `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles`
- `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/post-assets`

## Разделение статусов

Сервисные статусы статьи и draft-а:

- `draft`
- `pending_review`
- `awaiting_schedule`
- `awaiting_rejection_comment`
- `scheduled`
- `published`
- `rejected`
- `failed`

Локальные статусы в `publication-log.json`:

- `prepared`
- `pending_review`
- `awaiting_schedule`
- `awaiting_rejection_comment`
- `scheduled`
- `published`
- `rejected`
- `failed`
- `skipped_no_material`
- `skipped_quality`
- `skipped_blocked`
- `bypass_scheduled`
- `bypass_published`

Terminal-статусы для реально отправленных записей:

- `published`
- `rejected`

Важно:

- сервисный статус `draft` означает, что статья зарегистрирована локально, но еще не отправлена;
- локальный статус `prepared` означает, что материал собран, но отправка еще не завершена;
- `awaiting_rejection_comment` не является terminal-статусом и блокирует новый цикл;
- если у записи есть `draft_id` или `schedule_id`, ее статус нужно синхронизировать через API, а не угадывать по локальному файлу.

## Источники правил

Перед работой обязательно прочитай:

- `/Users/abetirov/projects/telegram-content-agent/.codex-local/post-drafts.md`
- `/Users/abetirov/projects/telegram-content-agent/.codex-local/publication-log.json`
- `/Users/abetirov/projects/telegram-content-agent/docs/publication-rules.md`
- `/Users/abetirov/projects/telegram-content-agent/docs/publishing-from-chat.md`

Если `publication-log.json` не существует, создай его как пустой JSON-массив `[]`.

## Порядок работы

### 1. Синхронизация существующего хвоста

Сначала синхронизируй локальный учет с сервисом:

- для каждой записи с `draft_id` запроси актуальный статус через draft API:
  - `GET /drafts/{draft_id}`
  - если запись отклонена или ожидает комментарий, дополнительно запроси `GET /drafts/{draft_id}/comments`
- для записи с `schedule_id` запроси актуальный статус через `GET /schedule/{schedule_id}`
- если у записи известен `article_id`, при необходимости проверь `GET /articles/{article_id}` и `GET /articles/{article_id}/comments`
- обнови `publication-log.json`
- обнови `post-drafts.md`

Если хотя бы один статус не удалось получить из-за API, сети, авторизации или недоступности сервиса:

- создай новую запись `skipped_blocked` с краткой причиной;
- обнови `publication-log.json`;
- добавь краткую запись в `post-drafts.md`;
- останови цикл.

### 2. Проверка неразобранного хвоста

Проверь два типа хвоста.

Локальный prepared-хвост:

- найди последние записи со статусом `prepared`;
- если у них нет `draft_id` и нет `schedule_id`, считай их неразобранным хвостом;
- если такой хвост есть, создай `skipped_blocked`, зафиксируй причину и останови цикл.

Telegram-хвост:

- возьми две последние записи, которые реально отправлялись в Telegram flow или bypass;
- если хотя бы одна из них имеет статус `failed`, создай `skipped_blocked` и останови цикл;
- если хотя бы одна из них не находится в terminal-статусах `published` или `rejected`, создай `skipped_blocked` и останови цикл.

### 3. Изучение материала в InComedy

Изучи релевантные изменения в `/Users/abetirov/AndroidStudioProjects/InComedy`:

- `git diff`
- связанные коммиты
- измененные файлы
- `docs/context`
- особенно `product`, `engineering`, `governance`, `handoff`

Если сильной темы нет:

- ничего не отправляй;
- запиши `skipped_no_material` в `publication-log.json`;
- добавь короткую запись в `post-drafts.md`;
- останови цикл.

### 4. Выбор темы

Выбери ровно одну тему.

Приоритет:

- продуктовые и архитектурные решения;
- сложные интеграции;
- изменения, влияющие на дальнейшее развитие проекта;
- реальные инженерные выводы.

Запрещено:

- собирать тему из россыпи мелких изменений;
- брать тему без опоры на первоисточники;
- писать материал, если он не выдерживает проверку по diff, коду и документам.

### 5. Проверка стиля

Сверь стиль по:

- `/Users/abetirov/projects/telegram-content-agent/.codex-local/post-drafts.md`
- `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles`
- `/Users/abetirov/projects/telegram-content-agent/.codex-local/articles`

Требования к стилю:

- одна главная тема;
- прямой русский язык;
- минимум жаргона;
- без длинных блоков кода;
- акцент на механизм, решение, риски и выводы.

### 6. Статья

Создай статью в `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/articles`:

- markdown;
- нормальный заголовок и структура;
- без больших блоков кода;
- вместо кода давай ссылки на классы, методы, документы и официальные источники;
- для внешних SDK, auth, платежей, интеграций и кабинетов провайдеров обязательно объясняй, где брать параметры, как использовать их в проекте и какие есть риски;
- если тема распадается на Android и iOS, не смешивай их.

### 7. Telegram-пост

Подготовь Telegram-пост:

- короткий сильный вывод, не пересказ статьи;
- простой язык;
- 1-3 внешние ссылки;
- если пост задуман как одно сообщение с одной картинкой, текст обязан помещаться в caption;
- если `link_style=text`, оставляй запас длины под URL.

### 8. Обложка

Сделай или обнови обложку в `/Users/abetirov/projects/telegram-content-agent/publications/InComedy/post-assets`:

- `1200x675`;
- чистая композиция;
- абстрактный фон;
- короткий сильный текст;
- без схем, перегруза и артефактов.

### 9. Три прохода редактуры

До любой отправки сделай три прогона:

- авторский черновик;
- упрощение и усиление пользы;
- редактура, сокращение, удаление AI-стиля.

### 10. Payload

Подготовь payload в `/Users/abetirov/projects/telegram-content-agent/.codex-local/payloads`.

### 11. Dry run и пред-валидация

Перед отправкой обязательно:

- если нужна локальная картинка, скопируй ее на сервер;
- сделай `dry_run` через `POST /publish` с `dry_run=true`;
- проверь стратегию публикации;
- если задуман single-message пост с одной картинкой, а `dry_run` показывает split, сократи текст и повтори `dry_run`;
- если `dry_run` неуспешен, создай `skipped_quality` и останови цикл;
- если не удается получить целостный single-message вариант для такого поста, создай `skipped_quality` и останови цикл.

### 12. Минимальные условия отправки

Не отправляй материал, если хотя бы одно условие не выполнено:

- тема сильная;
- текст основан на diff, коде, документах и официальных источниках;
- `dry_run` успешен;
- пост не разваливается, если задуман одним сообщением;
- приложены ключевые ссылки;
- текст зрелый.

### 13. Штатная отправка

Если все условия выполнены:

- используй `POST /drafts`;
- успешным результатом считай `pending_review`;
- сохрани `draft_id`;
- если API возвращает связанный `article_id`, сохрани и его;
- после отправки запроси `GET /drafts/{draft_id}` и зафиксируй точный статус;
- если есть сервисный `article_id`, запроси `GET /articles/{article_id}` и синхронизируй его статус;
- обнови `publication-log.json`;
- обнови `post-drafts.md`.

### 14. Bypass

Используй bypass только при явном основании.

Если используется `POST /schedule`:

- ставь время `18:05-18:47 Europe/Moscow`;
- `publish_at` должен быть в ISO 8601 с timezone;
- если время на сегодня прошло, ставь на следующий день;
- сохрани `schedule_id` и режим bypass.

### 15. Что сохранять в publication-log

Для каждой новой записи сохраняй:

- `slug`
- `topic`
- `article_path`
- `cover_path`
- `payload_path`
- `source_refs`
- `attached_links`
- `publication_mode`
- `draft_id`
- `draft_status`
- `article_id`
- `article_status`
- `schedule_id`
- `publish_at`
- `created_at`
- `updated_at`
- `failure_reason`

Если запись была отклонена и сервис вернул комментарии:

- сохрани краткую сводку комментария в `failure_reason` или notes-блок локального учета;
- не скрывай факт отклонения;
- не запускай новый цикл, пока reject-хвост не разобран и не доведен до terminal-состояния по локальным правилам.

### 16. Короткий финальный отчет

В конце дай короткий отчет:

- какая тема выбрана;
- на чем она основана;
- создана ли статья;
- подготовлен ли пост;
- пройден ли `dry_run`;
- отправлен ли draft;
- какой текущий статус;
- есть ли незавершенный хвост;
- если цикл пропущен, почему.

## Дополнительные ограничения

- ничего не публикуй напрямую в канал без явного основания;
- `pending_review` означает только успешную доставку в moderation flow;
- если сервисный `/articles` lifecycle виден, используй его как дополнительный источник правды, но главным результатом штатной отправки считай `POST /drafts -> pending_review`;
- если сервис недоступен, не придумывай статус, а явно фиксируй `skipped_blocked`;
- не подменяй реальные первоисточники интерпретацией без ссылки на код, doc или официальный внешний источник.
