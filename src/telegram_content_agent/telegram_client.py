from __future__ import annotations

import json
from contextlib import ExitStack
from html import escape
from pathlib import Path
from typing import Any

import httpx

from telegram_content_agent.config import Settings
from telegram_content_agent.models import ImageItem, LinkItem, LinkStyle, ParseMode, PublishRequest


class TelegramPublishError(RuntimeError):
    """Raised when Telegram Bot API returns an error."""


class TelegramPublisher:
    def __init__(
        self,
        settings: Settings,
        *,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._default_chat_id = default_chat_id or settings.telegram_channel_id
        self._base_url = (
            f"{settings.telegram_api_base.rstrip('/')}/bot{bot_token or settings.telegram_bot_token}"
        )
        self._client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def publish(self, request: PublishRequest) -> dict[str, Any]:
        link_style = request.link_style or self._settings.default_link_style
        chat_id = request.chat_id or self._default_chat_id
        rendered_text = self._render_text(
            text=request.text.strip(),
            links=request.links,
            link_style=link_style,
            parse_mode=request.parse_mode,
        )

        actions: list[dict[str, Any]] = []
        telegram_results: list[dict[str, Any]] = []

        if not request.images:
            strategy = "message"
            message_text = rendered_text or ("Полезные ссылки" if request.links else " ")
            payload = self._build_message_payload(
                chat_id=chat_id,
                text=message_text,
                request=request,
                link_style=link_style,
            )
            actions.append(self._summarize_action("sendMessage", payload))
            if not request.dry_run:
                telegram_results.append(await self._post_json("sendMessage", payload))
            return {
                "ok": True,
                "strategy": strategy,
                "rendered_text": rendered_text,
                "actions": actions,
                "telegram_results": telegram_results,
            }

        if len(request.images) == 1:
            strategy = "single-image"
            image = request.images[0]
            can_use_caption = bool(rendered_text) and len(rendered_text) <= 1024
            if can_use_caption:
                payload = self._build_photo_payload(
                    chat_id=chat_id,
                    image=image,
                    request=request,
                    caption=rendered_text,
                    link_style=link_style,
                )
                actions.append(self._summarize_action("sendPhoto", payload))
                if not request.dry_run:
                    telegram_results.append(
                        await self._post_photo("sendPhoto", payload, image=image)
                    )
            else:
                photo_payload = self._build_photo_payload(
                    chat_id=chat_id,
                    image=image,
                    request=request,
                    caption=None,
                    link_style=None,
                )
                actions.append(self._summarize_action("sendPhoto", photo_payload))
                if not request.dry_run:
                    telegram_results.append(
                        await self._post_photo("sendPhoto", photo_payload, image=image)
                    )
                if rendered_text or request.links:
                    message_payload = self._build_message_payload(
                        chat_id=chat_id,
                        text=rendered_text or "Полезные ссылки",
                        request=request,
                        link_style=link_style,
                    )
                    actions.append(self._summarize_action("sendMessage", message_payload))
                    if not request.dry_run:
                        telegram_results.append(
                            await self._post_json("sendMessage", message_payload)
                        )
            return {
                "ok": True,
                "strategy": strategy,
                "rendered_text": rendered_text,
                "actions": actions,
                "telegram_results": telegram_results,
            }

        strategy = "media-group"
        use_caption = (
            bool(rendered_text)
            and len(rendered_text) <= 1024
            and link_style != "buttons"
        )
        media_payload = self._build_media_group_payload(
            chat_id=chat_id,
            images=request.images,
            request=request,
            caption=rendered_text if use_caption else None,
        )
        actions.append(self._summarize_action("sendMediaGroup", media_payload))
        if not request.dry_run:
            telegram_results.append(
                await self._post_media_group(
                    "sendMediaGroup",
                    payload=media_payload,
                    images=request.images,
                )
            )
        if rendered_text and not use_caption:
            message_payload = self._build_message_payload(
                chat_id=chat_id,
                text=rendered_text,
                request=request,
                link_style=link_style,
            )
            actions.append(self._summarize_action("sendMessage", message_payload))
            if not request.dry_run:
                telegram_results.append(await self._post_json("sendMessage", message_payload))
        elif not rendered_text and request.links and link_style == "buttons":
            message_payload = self._build_message_payload(
                chat_id=chat_id,
                text="Полезные ссылки",
                request=request,
                link_style=link_style,
            )
            actions.append(self._summarize_action("sendMessage", message_payload))
            if not request.dry_run:
                telegram_results.append(await self._post_json("sendMessage", message_payload))
        return {
            "ok": True,
            "strategy": strategy,
            "rendered_text": rendered_text,
            "actions": actions,
            "telegram_results": telegram_results,
        }

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: ParseMode = "HTML",
        reply_markup: dict[str, Any] | str | None = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        protect_content: bool = False,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
            "disable_notification": disable_notification,
            "protect_content": protect_content,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = self._serialize_reply_markup(reply_markup)
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self._post_json("sendMessage", payload)

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: ParseMode = "HTML",
        reply_markup: dict[str, Any] | str | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = self._serialize_reply_markup(reply_markup)
        return await self._post_json("editMessageText", payload)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        return await self._post_json("answerCallbackQuery", payload)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 20,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = json.dumps(allowed_updates, ensure_ascii=False)
        response = await self._post_json("getUpdates", payload)
        result = response.get("result", [])
        if not isinstance(result, list):
            raise TelegramPublishError("Telegram getUpdates returned non-list result.")
        return result

    def _build_message_payload(
        self,
        *,
        chat_id: str,
        text: str,
        request: PublishRequest,
        link_style: LinkStyle | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": request.disable_web_page_preview,
            "disable_notification": request.disable_notification,
            "protect_content": request.protect_content,
        }
        if request.parse_mode:
            payload["parse_mode"] = request.parse_mode
        if link_style == "buttons" and request.links:
            payload["reply_markup"] = self._build_inline_keyboard(request.links)
        return payload

    def _build_photo_payload(
        self,
        *,
        chat_id: str,
        image: ImageItem,
        request: PublishRequest,
        caption: str | None,
        link_style: LinkStyle | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "disable_notification": request.disable_notification,
            "protect_content": request.protect_content,
        }
        if image.url is not None:
            payload["photo"] = str(image.url)
        if caption:
            payload["caption"] = caption
            if request.parse_mode:
                payload["parse_mode"] = request.parse_mode
        if link_style == "buttons" and request.links:
            payload["reply_markup"] = self._build_inline_keyboard(request.links)
        return payload

    def _build_media_group_payload(
        self,
        *,
        chat_id: str,
        images: list[ImageItem],
        request: PublishRequest,
        caption: str | None,
    ) -> dict[str, Any]:
        media: list[dict[str, Any]] = []
        for index, image in enumerate(images):
            item: dict[str, Any] = {"type": "photo"}
            if image.url is not None:
                item["media"] = str(image.url)
            else:
                item["media"] = f"attach://file{index}"
            if caption and index == 0:
                item["caption"] = caption
                if request.parse_mode:
                    item["parse_mode"] = request.parse_mode
            media.append(item)
        return {
            "chat_id": chat_id,
            "media": media,
            "disable_notification": request.disable_notification,
            "protect_content": request.protect_content,
        }

    def _render_text(
        self,
        *,
        text: str,
        links: list[LinkItem],
        link_style: LinkStyle | None,
        parse_mode: ParseMode,
    ) -> str:
        if link_style != "text" or not links:
            return text

        lines: list[str] = []
        if text:
            lines.append(text)
        lines.append("")
        lines.append("<b>Ссылки</b>" if parse_mode == "HTML" else "Ссылки")
        for link in links:
            if parse_mode == "HTML":
                lines.append(
                    f"• <a href=\"{escape(str(link.url), quote=True)}\">"
                    f"{escape(link.title)}</a>"
                )
            else:
                lines.append(f"- {link.title}: {link.url}")
        return "\n".join(lines).strip()

    def _build_inline_keyboard(self, links: list[LinkItem]) -> str:
        keyboard = [[{"text": link.title, "url": str(link.url)}] for link in links]
        return json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)

    async def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base_url}/{method}",
            data=self._serialize_form_payload(payload),
        )
        return self._handle_response(method, response)

    async def _post_photo(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        image: ImageItem,
    ) -> dict[str, Any]:
        if image.path is None:
            return await self._post_json(method, payload)

        path = self._validate_image_path(image.path)
        with path.open("rb") as file_handle:
            response = await self._client.post(
                f"{self._base_url}/{method}",
                data=self._serialize_form_payload(payload),
                files={"photo": (path.name, file_handle)},
            )
        return self._handle_response(method, response)

    async def _post_media_group(
        self,
        method: str,
        *,
        payload: dict[str, Any],
        images: list[ImageItem],
    ) -> dict[str, Any]:
        with ExitStack() as stack:
            files: dict[str, tuple[str, Any]] = {}
            serializable_payload = {
                "chat_id": payload["chat_id"],
                "media": json.dumps(payload["media"], ensure_ascii=False),
                "disable_notification": json.dumps(payload["disable_notification"]),
                "protect_content": json.dumps(payload["protect_content"]),
            }
            for index, image in enumerate(images):
                if image.path is None:
                    continue
                path = self._validate_image_path(image.path)
                files[f"file{index}"] = (path.name, stack.enter_context(path.open("rb")))
            response = await self._client.post(
                f"{self._base_url}/{method}",
                data=serializable_payload,
                files=files or None,
            )
        return self._handle_response(method, response)

    def _summarize_action(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        summary = dict(payload)
        if "reply_markup" in summary:
            summary["reply_markup"] = json.loads(summary["reply_markup"])
        if "media" in summary:
            summary["media"] = summary["media"]
        return {"method": method, "payload": summary}

    def _serialize_form_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, bool):
                serialized[key] = json.dumps(value)
            else:
                serialized[key] = value
        return serialized

    def _serialize_reply_markup(self, reply_markup: dict[str, Any] | str) -> str:
        if isinstance(reply_markup, str):
            return reply_markup
        return json.dumps(reply_markup, ensure_ascii=False)

    def _validate_image_path(self, image_path: Path) -> Path:
        if not image_path.is_absolute():
            image_path = image_path.resolve()
        if not image_path.exists():
            raise TelegramPublishError(f"Image file does not exist: {image_path}")
        if not image_path.is_file():
            raise TelegramPublishError(f"Image path is not a file: {image_path}")
        return image_path

    def _handle_response(self, method: str, response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise TelegramPublishError(
                f"Telegram API HTTP error during {method}: {error.response.text}"
            ) from error
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramPublishError(
                f"Telegram API rejected {method}: {payload.get('description', payload)}"
            )
        return payload
