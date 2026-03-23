# Деплой и GitHub Actions

## Что изменилось в runtime-модели

Теперь на сервере работает один FastAPI-процесс, но с двумя ботами:

- `publisher bot` публикует в основной канал
- `moderation bot` отправляет draft-ы в review-группу и получает update-ы через `getUpdates`

Webhook для moderation bot не нужен. Серверу достаточно исходящего доступа к Telegram API.

## Что уже есть в проекте

- [`.github/workflows/ci.yml`](/Users/abetirov/projects/telegram-content-agent/.github/workflows/ci.yml)
- [`.github/workflows/deploy.yml`](/Users/abetirov/projects/telegram-content-agent/.github/workflows/deploy.yml)
- [`scripts/deploy_remote.sh`](/Users/abetirov/projects/telegram-content-agent/scripts/deploy_remote.sh)
- [`deploy/systemd/telegram-content-agent.service.tpl`](/Users/abetirov/projects/telegram-content-agent/deploy/systemd/telegram-content-agent.service.tpl)

## Как работает CI

`CI` запускается:

- на `push` в `main`
- на каждый `pull_request`

Что делает:

- ставит Python `3.11` и `3.12`
- устанавливает проект через `pip install -e .[dev]`
- гоняет `python -m compileall src`
- запускает `pytest`

## Как работает CD

`Deploy` запускается:

- вручную через `workflow_dispatch`
- автоматически после успешного `CI` на `main`

Что делает:

1. Забирает код из GitHub
2. Подключается к серверу по SSH
3. Синхронизирует проект через `rsync`, не трогая `.env.production` и `data/`
4. На сервере обновляет `.venv` и зависимости
5. Генерирует `systemd` unit из шаблона
6. Перезапускает сервис

## Что хранить в GitHub

Рекомендуемый вариант: `Environment` с именем `production`.

### GitHub Secrets

- `DEPLOY_HOST` — IP или домен сервера
- `DEPLOY_USER` — пользователь для деплоя по SSH
- `DEPLOY_SSH_PRIVATE_KEY` — приватный SSH-ключ для GitHub Actions
- `DEPLOY_KNOWN_HOSTS` — строка из `ssh-keyscan -H your-host`

### GitHub Variables

- `DEPLOY_PORT` — SSH-порт, обычно `22`
- `DEPLOY_PATH` — путь к проекту на сервере, например `/opt/telegram-content-agent`
- `DEPLOY_ENV_FILE` — путь к env-файлу на сервере, например `/opt/telegram-content-agent/.env.production`
- `DEPLOY_SERVICE_NAME` — имя systemd-сервиса, например `telegram-content-agent`
- `DEPLOY_APP_HOST` — адрес биндинга приложения, рекомендую `127.0.0.1`
- `DEPLOY_APP_PORT` — порт приложения, например `8000`

## Что не хранить в GitHub

Эти значения лучше хранить только на сервере:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `MODERATION_BOT_TOKEN`
- `MODERATION_CHAT_ID`
- `MODERATION_ALLOWED_USER_IDS`
- `PUBLISH_API_TOKEN`
- любые внутренние URL и production-конфигурацию

## Где хранить production secrets на сервере

Рекомендованный путь:

```text
/opt/telegram-content-agent/.env.production
```

Пример содержимого:

```dotenv
TELEGRAM_BOT_TOKEN=123456:publisher_bot_token
TELEGRAM_CHANNEL_ID=@your_channel
MODERATION_BOT_TOKEN=123456:moderation_bot_token
MODERATION_CHAT_ID=-1001234567890
MODERATION_TIMEZONE=Europe/Moscow
MODERATION_ALLOWED_USER_IDS=123456789
MODERATION_POLL_INTERVAL_SECONDS=1
MODERATION_POLL_TIMEOUT_SECONDS=20
PUBLISH_API_TOKEN=long_random_secret
TELEGRAM_API_BASE=https://api.telegram.org
REQUEST_TIMEOUT_SECONDS=30
DEFAULT_PARSE_MODE=HTML
DEFAULT_LINK_STYLE=buttons
SCHEDULER_DB_PATH=/opt/telegram-content-agent/data/runtime.sqlite3
SCHEDULER_POLL_INTERVAL_SECONDS=5
SCHEDULER_BATCH_SIZE=10
SCHEDULER_RETRY_DELAY_SECONDS=60
SCHEDULER_MAX_ATTEMPTS=3
ARTICLES_ROOT_PATH=/opt/telegram-content-agent/publications
ARTICLES_AUTO_SYNC_ON_STARTUP=true
```

Права на файл:

```bash
sudo chown deploy-user:deploy-user /opt/telegram-content-agent/.env.production
sudo chmod 600 /opt/telegram-content-agent/.env.production
```

## Как подготовить сервер

На сервере должны быть:

- `python3`
- `python3-venv`
- `rsync`
- `systemd`
- `sudo` для deploy user без интерактивного пароля хотя бы на:
  - `install`
  - `systemctl daemon-reload`
  - `systemctl enable`
  - `systemctl restart`
  - `systemctl status`

Минимальная последовательность:

1. Создать пользователя для деплоя
2. Добавить его публичный SSH-ключ на сервер
3. Создать каталог приложения
4. Положить `.env.production`
5. Настроить `sudoers` для нужных команд
6. Оставить каталог `/opt/telegram-content-agent/data` вне очистки деплоем

## Практическая проверка после первого деплоя

```bash
sudo systemctl status telegram-content-agent
curl http://127.0.0.1:8000/health
ls -l /opt/telegram-content-agent/data
```

Ожидаемо:

- `health` возвращает `channel_id` и `moderation_chat_id`
- в `data/` появляется SQLite-файл runtime-состояния
- процесс стабильно держится как один `systemd`-service

## Важное по безопасности

- держите API за bearer-токеном
- не открывайте endpoint'ы в интернет без firewall или reverse proxy
- ограничьте состав участников `MODERATION_CHAT_ID`
- если в группе несколько человек, настройте `MODERATION_ALLOWED_USER_IDS`
