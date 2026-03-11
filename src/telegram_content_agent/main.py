from contextlib import asynccontextmanager
from secrets import compare_digest

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status

from telegram_content_agent.config import get_settings
from telegram_content_agent.models import PublishRequest, PublishResponse
from telegram_content_agent.telegram_client import TelegramPublishError, TelegramPublisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    publisher = TelegramPublisher(settings)
    app.state.publisher = publisher
    app.state.settings = settings
    try:
        yield
    finally:
        await publisher.aclose()


app = FastAPI(
    title="Telegram Content Agent",
    description="HTTP server for publishing channel posts via Telegram Bot API.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "channel_id": settings.telegram_channel_id,
    }


def verify_publish_token(
    authorization: str | None = Header(default=None),
) -> None:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )

    expected_header = f"Bearer {get_settings().publish_api_token}"
    if not compare_digest(authorization, expected_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
        )


@app.post("/publish", response_model=PublishResponse)
async def publish(
    request: PublishRequest,
    _: None = Depends(verify_publish_token),
) -> PublishResponse:
    publisher: TelegramPublisher = app.state.publisher
    try:
        result = await publisher.publish(request)
    except TelegramPublishError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return PublishResponse(**result)


def run() -> None:
    uvicorn.run(
        "telegram_content_agent.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
