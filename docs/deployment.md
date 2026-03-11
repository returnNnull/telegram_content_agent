# Деплой и GitHub Actions

## Что добавлено

В проекте есть два workflow:

- [`.github/workflows/ci.yml`](/Users/abetirov/projects/telegram-content-agent/.github/workflows/ci.yml) — установка зависимостей, компиляция и тесты
- [`.github/workflows/deploy.yml`](/Users/abetirov/projects/telegram-content-agent/.github/workflows/deploy.yml) — деплой на сервер по SSH и рестарт `systemd`-сервиса

Также добавлены:

- [`scripts/deploy_remote.sh`](/Users/abetirov/projects/telegram-content-agent/scripts/deploy_remote.sh) — удаленный deploy script
- [`deploy/systemd/telegram-content-agent.service.tpl`](/Users/abetirov/projects/telegram-content-agent/deploy/systemd/telegram-content-agent.service.tpl) — шаблон unit-файла

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
3. Синхронизирует проект через `rsync`, не трогая `.env.production`
4. На сервере обновляет `.venv` и зависимости
5. Генерирует `systemd` unit из шаблона
6. Перезапускает сервис

## Что хранить в GitHub

Лучше создать в GitHub не просто repository secrets, а `Environment` с именем `production`.

### GitHub Secrets

Эти значения храним в `Settings -> Secrets and variables -> Actions -> Environment secrets -> production`:

- `DEPLOY_HOST` — IP или домен сервера
- `DEPLOY_USER` — пользователь для деплоя по SSH
- `DEPLOY_SSH_PRIVATE_KEY` — приватный SSH-ключ для GitHub Actions
- `DEPLOY_KNOWN_HOSTS` — строка из `ssh-keyscan -H your-host`

### GitHub Variables

Эти значения можно хранить в `Environment variables` для `production`:

- `DEPLOY_PORT` — SSH-порт, обычно `22`
- `DEPLOY_PATH` — путь к проекту на сервере, например `/opt/telegram-content-agent`
- `DEPLOY_ENV_FILE` — путь к env-файлу на сервере, например `/opt/telegram-content-agent/.env.production`
- `DEPLOY_SERVICE_NAME` — имя systemd-сервиса, например `telegram-content-agent`
- `DEPLOY_APP_HOST` — адрес биндинга приложения, рекомендую `127.0.0.1`
- `DEPLOY_APP_PORT` — порт приложения, например `8000`

## Что НЕ хранить в GitHub

Вот это лучше хранить только на сервере, а не в GitHub Actions:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `PUBLISH_API_TOKEN`
- любые внутренние URL и production-конфигурацию

Причина простая: workflow не должен знать Telegram-секреты, если его задача только выкатить код и перезапустить сервис.

## Где хранить production secrets на сервере

Рекомендованный путь:

```text
/opt/telegram-content-agent/.env.production
```

Пример содержимого:

```dotenv
TELEGRAM_BOT_TOKEN=123456:real_bot_token
TELEGRAM_CHANNEL_ID=@your_channel
PUBLISH_API_TOKEN=long_random_secret
TELEGRAM_API_BASE=https://api.telegram.org
REQUEST_TIMEOUT_SECONDS=30
DEFAULT_PARSE_MODE=HTML
DEFAULT_LINK_STYLE=buttons
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

Минимальный путь настройки:

1. Создать пользователя для деплоя
2. Добавить его публичный SSH-ключ на сервер
3. Создать каталог приложения
4. Положить `.env.production`
5. Настроить `sudoers` для нужных команд

## Что сделать в GitHub после добавления файлов

1. Открыть репозиторий на GitHub
2. Перейти в `Settings -> Environments`
3. Создать environment `production`
4. Заполнить `Secrets` и `Variables`
5. При желании включить required reviewers для ручного подтверждения deploy

## Практическая рекомендация

Для первого запуска:

1. Заполни `production` secrets и variables в GitHub
2. Подготовь `.env.production` на сервере
3. Запусти workflow `Deploy` вручную
4. Проверь на сервере:

```bash
sudo systemctl status telegram-content-agent
curl http://127.0.0.1:8000/health
```

## Важное замечание по безопасности

Если сервис будет доступен не только локально, обязательно:

- держи `POST /publish` за bearer-токеном
- ограничь доступ по firewall или reverse proxy
- не открывай endpoint в интернет без дополнительной защиты
