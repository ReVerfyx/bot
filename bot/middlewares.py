"""Middleware: регистрация пользователя, бан-лист, техработы, антифлуд."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TgUser

from .config import Config
from .storage import Repository, User

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Кладёт в data репозиторий, конфиг и модель пользователя."""

    def __init__(self, cfg: Config, repo: Repository) -> None:
        self.cfg = cfg
        self.repo = repo

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        data["cfg"] = self.cfg
        data["repo"] = self.repo
        if tg_user is None or tg_user.is_bot:
            # апдейт без автора (пост в канале и т.п.) — заглушки, чтобы не падать
            data.setdefault("is_admin", False)
            data.setdefault("user", User(id=0))
            return await handler(event, data)

        is_admin = self.cfg.is_admin(tg_user.id)
        data["is_admin"] = is_admin
        user = await self.repo.touch_user(
            tg_user.id, (tg_user.full_name or "").strip(), tg_user.username or "")
        data["user"] = user

        if user.banned and not is_admin:
            await self._deny(event, self.cfg.text("banned"))
            return None
        if self.cfg.get("access.maintenance", False) and not is_admin:
            await self._deny(event, self.cfg.text("maintenance"))
            return None
        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
        except Exception as exc:                     # noqa: BLE001
            log.debug("Не смог отправить отказ: %s", exc)


class ThrottleMiddleware(BaseMiddleware):
    """Простейший антифлуд: не чаще одного апдейта в `rate` секунд на юзера."""

    def __init__(self, rate: float = 0.4) -> None:
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is not None:
            now = time.monotonic()
            if now - self._last.get(tg_user.id, 0.0) < self.rate:
                if isinstance(event, CallbackQuery):
                    await event.answer("Слишком быстро 🙂")
                return None
            self._last[tg_user.id] = now
            if len(self._last) > 10_000:
                self._last = {k: v for k, v in self._last.items() if now - v < 60}
        return await handler(event, data)
