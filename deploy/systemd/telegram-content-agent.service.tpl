[Unit]
Description=Telegram Content Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__APP_USER__
Group=__APP_USER__
WorkingDirectory=__APP_DIR__
EnvironmentFile=__ENV_FILE__
Environment=PYTHONUNBUFFERED=1
ExecStart=__APP_DIR__/.venv/bin/uvicorn telegram_content_agent.main:app --host __APP_HOST__ --port __APP_PORT__
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
