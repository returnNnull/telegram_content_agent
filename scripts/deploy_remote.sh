#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:?APP_DIR is required}"
APP_USER="${APP_USER:?APP_USER is required}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"
SERVICE_NAME="${SERVICE_NAME:-telegram-content-agent}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "Application directory does not exist: $APP_DIR" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file does not exist: $ENV_FILE" >&2
  exit 1
fi

cd "$APP_DIR"

if [[ ! -d ".venv" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

service_tmp="$(mktemp)"
sed \
  -e "s|__APP_USER__|$APP_USER|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__ENV_FILE__|$ENV_FILE|g" \
  -e "s|__APP_HOST__|$APP_HOST|g" \
  -e "s|__APP_PORT__|$APP_PORT|g" \
  "$APP_DIR/deploy/systemd/telegram-content-agent.service.tpl" > "$service_tmp"

sudo install -m 644 "$service_tmp" "/etc/systemd/system/$SERVICE_NAME.service"
rm -f "$service_tmp"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager --lines=20
