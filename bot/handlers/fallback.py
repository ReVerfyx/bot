"""Всё, что не подошло ни одному хендлеру."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..config import Config

router = Router(name="fallback")


@router.message(F.chat.type == "private")
async def unknown_message(message: Message, cfg: Config, is_admin: bool = False) -> None:
    await message.answer(cfg.text("unknown"), reply_markup=ui.main_menu(cfg, is_admin))


@router.callback_query()
async def stale_callback(call: CallbackQuery, cfg: Config, is_admin: bool = False) -> None:
    """Кнопка из старого сообщения после рестарта — возвращаем в меню."""
    await call.answer("Кнопка устарела, обновил меню")
    if isinstance(call.message, Message):
        await call.message.answer(cfg.text("menu"), reply_markup=ui.main_menu(cfg, is_admin))
