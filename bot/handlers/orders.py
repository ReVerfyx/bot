"""Создание заказа из каталога, список заказов клиента, карточка, отмена."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from .. import ui
from ..common import show
from ..config import Config
from ..services.cryptobot import CryptoPay
from ..storage import Order, Repository, User
from ..storage.models import CANCELLED, EXPIRED, NEW, age_minutes
from .payment import offer_payment

log = logging.getLogger(__name__)
router = Router(name="orders")


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                 crypto: CryptoPay, state: FSMContext) -> None:
    await state.clear()
    parts = call.data.split(":")

    if parts[1] == "retry":                       # вернуться к оплате старого заказа
        order = await repo.get_order(int(parts[2]))
        if not order or order.user_id != user.id or order.status != NEW:
            await call.answer("Заказ уже неактуален", show_alert=True)
            return
        await offer_payment(call, cfg, order, crypto)
        await call.answer()
        return

    product_id = parts[1]
    section, item = cfg.find_product(product_id)
    if not item:
        await call.answer("Позиция не найдена", show_alert=True)
        return

    order = await repo.create_order(Order(
        user_id=user.id,
        user_name=user.name,
        username=user.username,
        kind="scooter" if section == "scooters" else "goods",
        product_id=product_id,
        title=str(item.get("title", "")),
        amount=int(item.get("price", 0)),
        currency=cfg.currency,
    ))
    log.info("Заказ #%s создан пользователем %s", order.id, user.id)
    await offer_payment(call, cfg, order, crypto)
    await call.answer()


@router.callback_query(F.data == "m:orders")
async def cb_orders(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                    state: FSMContext) -> None:
    await state.clear()
    orders = await repo.user_orders(user.id, limit=10)
    orders = [await _expire_if_needed(cfg, repo, o) for o in orders]
    if not orders:
        await show(call, cfg.text("orders_empty"), ui.main_menu(cfg, cfg.is_admin(user.id)))
        await call.answer()
        return
    await show(call, cfg.text("orders_header"), ui.orders_list(cfg, orders))
    await call.answer()


async def _expire_if_needed(cfg: Config, repo: Repository, order: Order) -> Order:
    ttl = int(cfg.get("payment.order_ttl_minutes", 120))
    if order.status == NEW and age_minutes(order.created_at) > ttl:
        order.status = EXPIRED
        await repo.save_order(order, event="просрочен, оплата не поступила")
    return order


@router.callback_query(F.data.startswith("ord:"))
async def cb_order_card(call: CallbackQuery, cfg: Config, repo: Repository, user: User) -> None:
    order = await repo.get_order(int(call.data.split(":", 1)[1]))
    if not order or (order.user_id != user.id and not cfg.is_admin(user.id)):
        await call.answer("Заказ не найден", show_alert=True)
        return
    order = await _expire_if_needed(cfg, repo, order)
    extra = []
    if order.item_url:
        extra.append(f'{cfg.emoji("link")} <a href="{order.item_url}">товар</a>')
    if order.address:
        extra.append(f"{cfg.emoji('pin')} {order.delivery_title}: {order.address}")
    if order.code:
        extra.append(f"{cfg.emoji('box')} код: <code>{order.code}</code>")
    if order.reason:
        extra.append(f"💬 {order.reason}")
    text = cfg.text(
        "order_card",
        order_id=order.id,
        title=order.title,
        status=cfg.status_label(order.status),
        amount=cfg.money(order.amount),
        created=order.created_at.replace("T", " ")[:16],
        extra="\n".join(extra),
    )
    window = int(cfg.get("refund.window_hours", 72))
    await show(call, text, ui.order_card(cfg, order, window))
    await call.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                    state: FSMContext) -> None:
    await state.clear()
    order = await repo.get_order(int(call.data.split(":", 1)[1]))
    if not order or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status != NEW:
        await call.answer("Такой заказ уже нельзя отменить", show_alert=True)
        return
    order.status = CANCELLED
    await repo.save_order(order, event="отменён клиентом")
    await show(call, cfg.text("cancelled"), ui.main_menu(cfg, cfg.is_admin(user.id)))
    await call.answer()
