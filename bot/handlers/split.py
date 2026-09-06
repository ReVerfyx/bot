"""Выкуп по Сплиту: клиент присылает ссылку — бот считает комиссию сервиса."""

from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import show
from ..config import Config
from ..services.cryptobot import CryptoPay
from ..services.split_parser import domain_allowed, extract_url, fetch_item
from ..states import SplitFlow
from ..storage import Order, Repository, User
from .payment import offer_payment

log = logging.getLogger(__name__)
router = Router(name="split")


def _limits(cfg: Config) -> tuple[int, int]:
    return int(cfg.get("split.min_price", 0)), int(cfg.get("split.max_price", 150_000))


async def _show_card(event: Message | CallbackQuery, cfg: Config, state: FSMContext) -> None:
    data = await state.get_data()
    price = int(data.get("price", 0))
    fee = cfg.split_fee(price) or 0
    await state.update_data(fee=fee)
    text = cfg.text(
        "split_card",
        title=data.get("title") or "Товар по ссылке",
        url=data.get("url", ""),
        item_price=cfg.money(price),
        fee=cfg.money(fee),
        total=cfg.money(fee),
    )
    await show(event, text, ui.split_confirm(cfg))


@router.callback_query(F.data == "m:split")
async def cb_split(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.clear()
    _, max_price = _limits(cfg)
    await show(call, cfg.text("split_intro", tiers=cfg.split_tiers_text(),
                              max=cfg.money(max_price)), ui.split_start(cfg))
    await call.answer()


@router.callback_query(F.data == "sp:go")
async def cb_ask_link(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.set_state(SplitFlow.link)
    await show(call, cfg.text("split_ask_link"), ui.cancel_only(cfg))
    await call.answer()


@router.message(SplitFlow.link)
async def msg_link(message: Message, cfg: Config, state: FSMContext) -> None:
    allowed = list(cfg.get("split.allowed_domains", []) or [])
    url = extract_url(message.text or "")
    if not url or not domain_allowed(url, allowed):
        await message.answer(cfg.text("split_bad_link", domains=", ".join(allowed)),
                             reply_markup=ui.cancel_only(cfg))
        return

    note = await message.answer(cfg.text("split_fetching"))
    info = await fetch_item(url, timeout=int(cfg.get("split.fetch_timeout", 12)))
    with suppress(TelegramBadRequest):
        await note.delete()

    await state.update_data(url=info.url or url, title=info.title, image=info.image)
    min_price, max_price = _limits(cfg)

    if not info.price:
        await state.set_state(SplitFlow.price)
        await message.answer(cfg.text("split_ask_price"), reply_markup=ui.cancel_only(cfg))
        return
    if info.price > max_price:
        await state.clear()
        await message.answer(cfg.text("split_price_high", max=cfg.money(max_price)),
                             reply_markup=ui.main_menu(cfg, cfg.is_admin(message.from_user.id)))
        return
    if info.price < min_price:
        await state.set_state(SplitFlow.price)
        await message.answer(cfg.text("split_price_low", min=cfg.money(min_price)),
                             reply_markup=ui.cancel_only(cfg))
        return

    await state.update_data(price=info.price)
    await state.set_state(SplitFlow.price)
    await _show_card(message, cfg, state)


@router.message(SplitFlow.price)
async def msg_price(message: Message, cfg: Config, state: FSMContext) -> None:
    raw = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if not raw:
        await message.answer(cfg.text("split_bad_price"), reply_markup=ui.cancel_only(cfg))
        return
    price = int(raw)
    min_price, max_price = _limits(cfg)
    if price < min_price:
        await message.answer(cfg.text("split_price_low", min=cfg.money(min_price)),
                             reply_markup=ui.cancel_only(cfg))
        return
    if price > max_price:
        await state.clear()
        await message.answer(cfg.text("split_price_high", max=cfg.money(max_price)),
                             reply_markup=ui.main_menu(cfg, cfg.is_admin(message.from_user.id)))
        return
    await state.update_data(price=price)
    await _show_card(message, cfg, state)


@router.callback_query(F.data == "sp:conf")
async def cb_confirm(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("price"):
        await call.answer("Начни заново — пришли ссылку", show_alert=True)
        return
    await show(call, cfg.text("split_ask_delivery"), ui.split_delivery(cfg))
    await call.answer()


@router.callback_query(F.data.startswith("sp:dlv:"))
async def cb_delivery(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    method_id = call.data.split(":", 2)[2]
    method = next((m for m in (cfg.get("split.delivery", []) or [])
                   if str(m.get("id")) == method_id), None)
    if not method:
        await call.answer("Способ доставки не найден", show_alert=True)
        return
    await state.update_data(delivery_id=method_id, delivery_title=str(method.get("title", "")))
    await state.set_state(SplitFlow.address)
    await show(call, cfg.text("split_ask_address", ask=method.get("ask", "Укажи адрес:")),
               ui.cancel_only(cfg))
    await call.answer()


@router.message(SplitFlow.address)
async def msg_address(message: Message, cfg: Config, repo: Repository, user: User,
                      crypto: CryptoPay, state: FSMContext) -> None:
    address = (message.text or "").strip()
    if len(address) < 10:
        await message.answer(cfg.text("split_address_short"), reply_markup=ui.cancel_only(cfg))
        return
    data = await state.get_data()
    await state.clear()

    price = int(data.get("price", 0))
    fee = int(data.get("fee") or cfg.split_fee(price) or 0)
    title = data.get("title") or "Товар по ссылке"
    order = await repo.create_order(Order(
        user_id=user.id,
        user_name=user.name,
        username=user.username,
        kind="split",
        product_id="split",
        title=f"Сплит · {title}"[:120],
        amount=fee,
        currency=cfg.currency,
        item_url=str(data.get("url", "")),
        item_title=str(title),
        item_price=price,
        fee=fee,
        delivery_id=str(data.get("delivery_id", "")),
        delivery_title=str(data.get("delivery_title", "")),
        address=address[:400],
    ))
    log.info("Сплит-заказ #%s создан пользователем %s", order.id, user.id)
    await offer_payment(message, cfg, order, crypto)
