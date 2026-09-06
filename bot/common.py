"""Общие помощники для хендлеров: показ экранов и уведомления админов."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .config import Config
from .storage import Order, Repository
from .storage.models import ACTIVATION, PAID
from . import ui

log = logging.getLogger(__name__)


async def show(event: Message | CallbackQuery, text: str,
               markup: InlineKeyboardMarkup | None = None, *,
               disable_preview: bool = True) -> None:
    """Показать экран: коллбэк — правим сообщение, иначе шлём новое."""
    if isinstance(event, CallbackQuery):
        message = event.message
        if not isinstance(message, Message):      # старое или недоступное сообщение
            if message is not None:
                await event.bot.send_message(message.chat.id, text, reply_markup=markup,
                                             disable_web_page_preview=disable_preview)
            return
        try:
            await message.edit_text(text, reply_markup=markup,
                                    disable_web_page_preview=disable_preview)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            # сообщение с фото или уже удалено — отправляем новое
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
            await message.answer(text, reply_markup=markup,
                                 disable_web_page_preview=disable_preview)
            return
    await event.answer(text, reply_markup=markup, disable_web_page_preview=disable_preview)


async def send_sticker(bot: Bot, cfg: Config, chat_id: int, key: str) -> None:
    file_id = cfg.sticker(key)
    if not file_id:
        return
    try:
        await bot.send_sticker(chat_id, file_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        log.debug("Стикер %s не отправлен: %s", key, exc)


async def safe_send(bot: Bot, chat_id: int, text: str,
                    markup: InlineKeyboardMarkup | None = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup,
                               disable_web_page_preview=True)
        return True
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        return await safe_send(bot, chat_id, text, markup)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        log.info("Не доставлено в чат %s: %s", chat_id, exc)
        return False


# ════════════════════════ карточка заказа для админа ════════════════════════
def admin_order_text(cfg: Config, order: Order) -> str:
    extra_lines = []
    if order.item_url:
        extra_lines.append(f'🔗 <a href="{order.item_url}">товар</a> · {cfg.money(order.item_price)}')
    if order.address:
        extra_lines.append(f"{cfg.emoji('pin')} {order.delivery_title}: {order.address}")
    if order.proof:
        extra_lines.append(f"🧾 <code>{order.proof}</code>")
    if order.proof_file_id:
        extra_lines.append("🖼 скриншот оплаты прикреплён")
    if order.code:
        extra_lines.append(f"📦 выдан код: <code>{order.code}</code>")
    if order.activation_text:
        extra_lines.append(f"🛴 от клиента: <code>{order.activation_text}</code>")
    if order.activation_file_id:
        extra_lines.append("🖼 скриншот активации прикреплён")
    if order.refund_wallet:
        extra_lines.append(f"👛 кошелёк возврата: <code>{order.refund_wallet}</code>")
    if order.reason:
        extra_lines.append(f"💬 {order.reason}")
    return cfg.text(
        "admin_order",
        order_id=order.id,
        status=cfg.status_label(order.status),
        user_id=order.user_id,
        user_name=order.user_name or order.username or order.user_id,
        title=order.title,
        amount=cfg.money(order.amount),
        method=order.wallet_title or order.method or "—",
        created=order.created_at.replace("T", " ")[:16],
        extra="\n".join(extra_lines),
    )


async def notify_admins(bot: Bot, cfg: Config, text: str,
                        markup: InlineKeyboardMarkup | None = None,
                        photo_id: str = "") -> None:
    targets: list[int] = []
    if cfg.admin_chat_id:
        targets.append(cfg.admin_chat_id)
    targets.extend(a for a in cfg.admins if a not in targets)
    for chat_id in targets:
        try:
            if photo_id:
                await bot.send_photo(chat_id, photo_id, caption=text[:1024], reply_markup=markup)
            else:
                await bot.send_message(chat_id, text, reply_markup=markup,
                                       disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            log.warning("Админ-уведомление в %s не доставлено: %s", chat_id, exc)


async def notify_order(bot: Bot, cfg: Config, order: Order, header: str = "") -> None:
    text = (f"{header}\n\n" if header else "") + admin_order_text(cfg, order)
    await notify_admins(bot, cfg, text, ui.admin_order_card(cfg, order),
                        photo_id=order.proof_file_id or order.activation_file_id)


# ════════════════════════ выдача после подтверждения ════════════════════════
async def confirm_payment(bot: Bot, cfg: Config, repo: Repository, order: Order) -> Order:
    """Оператор подтвердил оплату: резервируем код и открываем клиенту активацию."""
    _, item = cfg.find_product(order.product_id)
    sku = str((item or {}).get("sku") or "")
    if sku and not order.code:
        code = await repo.stock_take(sku)
        if code:
            order.code = code
            order.log(f"выдан код со склада {sku}")
    order.status = PAID
    await repo.save_order(order, event="оплата подтверждена оператором")

    await send_sticker(bot, cfg, order.user_id, "paid")
    await safe_send(
        bot, order.user_id,
        cfg.text("order_confirmed", order_id=order.id, title=order.title),
        ui.activation_menu(cfg, order.id),
    )
    order.status = ACTIVATION
    await repo.save_order(order, event="ждём данные от клиента")
    return order
