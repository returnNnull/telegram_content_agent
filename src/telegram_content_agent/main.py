from datetime import UTC, datetime
from contextlib import asynccontextmanager
from secrets import compare_digest

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from telegram_content_agent.articles import ArticleNotFoundError, ArticleStore
from telegram_content_agent.config import get_settings
from telegram_content_agent.moderation import (
    ModerationBotRunner,
    ModerationDraftNotFoundError,
    ModerationService,
    ModerationStore,
)
from telegram_content_agent.models import (
    ArticleCommentResponse,
    ArticleDryRunResponse,
    ArticleResponse,
    ArticleSnapshotRequest,
    ArticleStatus,
    ArticleSubmitResponse,
    ModerationDraftResponse,
    ModerationDraftStatus,
    PublishRequest,
    PublishResponse,
    SchedulePublishRequest,
    ScheduledPostResponse,
    ScheduledPostStatus,
    SubmitDraftRequest,
)
from telegram_content_agent.scheduler import (
    ScheduledPostConflictError,
    ScheduledPostNotFoundError,
    ScheduledPostStore,
    ScheduledPublisher,
)
from telegram_content_agent.telegram_client import TelegramPublishError, TelegramPublisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    publisher = TelegramPublisher(
        settings,
        bot_token=settings.telegram_bot_token,
        default_chat_id=settings.telegram_channel_id,
    )
    moderation_publisher = TelegramPublisher(
        settings,
        bot_token=settings.moderation_bot_token,
        default_chat_id=settings.moderation_chat_id,
    )
    scheduler_store = ScheduledPostStore(settings.scheduler_db_path)
    moderation_store = ModerationStore(settings.scheduler_db_path)
    article_store = ArticleStore(settings.scheduler_db_path)
    scheduler_store.initialize()
    moderation_store.initialize()
    article_store.initialize()
    scheduler_store.recover_processing_posts()
    scheduler = ScheduledPublisher(settings=settings, publisher=publisher, store=scheduler_store)
    moderation_service = ModerationService(
        settings=settings,
        store=moderation_store,
        article_store=article_store,
        moderation_publisher=moderation_publisher,
        channel_publisher=publisher,
        scheduler=scheduler,
    )
    scheduler.set_event_handlers(
        on_post_published=moderation_service.sync_scheduled_post,
        on_post_failed=moderation_service.sync_scheduled_post,
    )
    moderation_runner = ModerationBotRunner(
        settings=settings,
        moderation_publisher=moderation_publisher,
        moderation_service=moderation_service,
    )
    await scheduler.start()
    await moderation_runner.start()
    app.state.publisher = publisher
    app.state.moderation_publisher = moderation_publisher
    app.state.scheduler = scheduler
    app.state.moderation_store = moderation_store
    app.state.article_store = article_store
    app.state.moderation_service = moderation_service
    app.state.moderation_runner = moderation_runner
    app.state.settings = settings
    try:
        yield
    finally:
        await moderation_runner.stop()
        await scheduler.stop()
        await moderation_publisher.aclose()
        await publisher.aclose()


app = FastAPI(
    title="Telegram Content Agent",
    description=(
        "HTTP server for article-centric Telegram moderation and publication "
        "with separate review and channel bots."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "channel_id": settings.telegram_channel_id,
        "moderation_chat_id": settings.moderation_chat_id,
        "server_time": datetime.now(UTC).isoformat(),
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


@app.post("/articles/dry-run", response_model=ArticleDryRunResponse)
async def article_dry_run(
    request: ArticleSnapshotRequest,
    _: None = Depends(verify_publish_token),
) -> ArticleDryRunResponse:
    publisher: TelegramPublisher = app.state.publisher
    article_store: ArticleStore = app.state.article_store
    try:
        dry_run_result = await publisher.publish(
            request.payload.model_copy(update={"dry_run": True, "chat_id": None})
        )
    except TelegramPublishError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    article = article_store.upsert_snapshot(
        request,
        status="draft",
        publish_strategy=dry_run_result["strategy"],
        last_error=None,
    )
    return ArticleDryRunResponse(
        article=article.to_response(),
        dry_run=PublishResponse(**dry_run_result),
    )


@app.post("/articles/submit", response_model=ArticleSubmitResponse)
async def submit_article(
    request: ArticleSnapshotRequest,
    _: None = Depends(verify_publish_token),
) -> ArticleSubmitResponse:
    article_store: ArticleStore = app.state.article_store
    moderation_service: ModerationService = app.state.moderation_service
    article = article_store.upsert_snapshot(
        request,
        status="draft",
        publish_strategy=request.publish_strategy,
        last_error=None,
    )
    try:
        draft = await moderation_service.submit_article(
            article_id=article.article_id,
            request=article.payload,
        )
    except TelegramPublishError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    refreshed_article = article_store.get(article.article_id)
    return ArticleSubmitResponse(article=refreshed_article, draft=draft)


@app.post("/drafts", response_model=ModerationDraftResponse)
async def submit_draft(
    request: SubmitDraftRequest,
    _: None = Depends(verify_publish_token),
) -> ModerationDraftResponse:
    moderation_service: ModerationService = app.state.moderation_service
    try:
        return await moderation_service.submit_draft(request)
    except TelegramPublishError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/drafts", response_model=list[ModerationDraftResponse])
async def list_drafts(
    status_filter: ModerationDraftStatus | None = Query(default=None, alias="status"),
    _: None = Depends(verify_publish_token),
) -> list[ModerationDraftResponse]:
    moderation_store: ModerationStore = app.state.moderation_store
    return moderation_store.list(status=status_filter)


@app.get("/drafts/{draft_id}", response_model=ModerationDraftResponse)
async def get_draft(
    draft_id: str,
    _: None = Depends(verify_publish_token),
) -> ModerationDraftResponse:
    moderation_store: ModerationStore = app.state.moderation_store
    try:
        return moderation_store.get(draft_id)
    except ModerationDraftNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/drafts/{draft_id}/comments", response_model=list[ArticleCommentResponse])
async def list_draft_comments(
    draft_id: str,
    _: None = Depends(verify_publish_token),
) -> list[ArticleCommentResponse]:
    article_store: ArticleStore = app.state.article_store
    return article_store.list_comments(draft_id=draft_id)


@app.get("/articles", response_model=list[ArticleResponse])
async def list_articles(
    status_filter: ArticleStatus | None = Query(default=None, alias="status"),
    _: None = Depends(verify_publish_token),
) -> list[ArticleResponse]:
    article_store: ArticleStore = app.state.article_store
    return article_store.list(status=status_filter)


@app.get("/articles/{article_id}", response_model=ArticleResponse)
async def get_article(
    article_id: str,
    _: None = Depends(verify_publish_token),
) -> ArticleResponse:
    article_store: ArticleStore = app.state.article_store
    try:
        return article_store.get(article_id)
    except ArticleNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/articles/{article_id}/comments", response_model=list[ArticleCommentResponse])
async def list_article_comments(
    article_id: str,
    _: None = Depends(verify_publish_token),
) -> list[ArticleCommentResponse]:
    article_store: ArticleStore = app.state.article_store
    return article_store.list_comments(article_id=article_id)


@app.post("/schedule", response_model=ScheduledPostResponse)
async def schedule_publish(
    request: SchedulePublishRequest,
    _: None = Depends(verify_publish_token),
) -> ScheduledPostResponse:
    scheduler: ScheduledPublisher = app.state.scheduler
    publish_request = PublishRequest.model_validate(request.model_dump(exclude={"publish_at"}))
    return scheduler.create(request=publish_request, publish_at=request.publish_at)


@app.get("/schedule", response_model=list[ScheduledPostResponse])
async def list_scheduled_posts(
    status_filter: ScheduledPostStatus | None = Query(default=None, alias="status"),
    _: None = Depends(verify_publish_token),
) -> list[ScheduledPostResponse]:
    scheduler: ScheduledPublisher = app.state.scheduler
    return scheduler.list(status=status_filter)


@app.get("/schedule/{post_id}", response_model=ScheduledPostResponse)
async def get_scheduled_post(
    post_id: str,
    _: None = Depends(verify_publish_token),
) -> ScheduledPostResponse:
    scheduler: ScheduledPublisher = app.state.scheduler
    try:
        return scheduler.get(post_id)
    except ScheduledPostNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/schedule/{post_id}", response_model=ScheduledPostResponse)
async def cancel_scheduled_post(
    post_id: str,
    _: None = Depends(verify_publish_token),
) -> ScheduledPostResponse:
    scheduler: ScheduledPublisher = app.state.scheduler
    try:
        return scheduler.cancel(post_id)
    except ScheduledPostNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ScheduledPostConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def run() -> None:
    uvicorn.run(
        "telegram_content_agent.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
