"""Страховка на кастомные эмодзи (Bot API 9.4).

Премиум-эмодзи в текстах и иконки на кнопках работают, только пока у владельца
бота активна подписка Telegram Premium, а все custom_emoji_id валидны. Если
подписка кончится или в config.yml окажется битый id, Telegram начнёт отбивать
КАЖДОЕ сообщение — то есть магазин просто встанет.

Эта session-middleware ловит такую ошибку, повторяет запрос без кастомных
эмодзи и дальше отправляет всё уже в обычном виде: клиент видит стандартные
эмодзи вместо премиальных, но покупает как ни в чём не бывало.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import NextRequestMiddlewareType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

log = logging.getLogger(__name__)

TG_EMOJI_TAG = re.compile(r"</?tg-emoji[^>]*>")
MARKERS = ("custom emoji", "custom_emoji", "customemoji", "premium")


def _looks_like_emoji_error(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in MARKERS)


def _strip(method: TelegramMethod[TelegramType]) -> bool:
    """Убрать премиум-эмодзи из запроса. True — что-то действительно убрали."""
    changed = False
    for field in ("text", "caption"):
        value = getattr(method, field, None)
        if isinstance(value, str) and "<tg-emoji" in value:
            setattr(method, field, TG_EMOJI_TAG.sub("", value))
            changed = True
    markup: Any = getattr(method, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            if getattr(button, "icon_custom_emoji_id", None):
                button.icon_custom_emoji_id = None
                changed = True
    for row in getattr(markup, "keyboard", None) or []:
        for button in row:
            if getattr(button, "icon_custom_emoji_id", None):
                button.icon_custom_emoji_id = None
                changed = True
    return changed


class CustomEmojiGuard:
    """Один раз обжигается на ошибке и дальше чистит запросы превентивно."""

    def __init__(self) -> None:
        self.disabled = False

    async def __call__(self, make_request: NextRequestMiddlewareType[TelegramType],
                       bot: Bot, method: TelegramMethod[TelegramType]) -> TelegramType:
        if self.disabled:
            _strip(method)
            return await make_request(bot, method)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if not _looks_like_emoji_error(exc) or not _strip(method):
                raise
            self.disabled = True
            log.warning(
                "Telegram отказал в кастомных эмодзи (%s). Отключаю их до конца смены — "
                "проверь Telegram Premium у владельца бота и id в decor.custom_emoji", exc,
            )
            return await make_request(bot, method)
