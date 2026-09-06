"""Поддержка и возвраты со стороны клиента."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import notify_admins, notify_order, send_sticker, show
from ..config import Config
from ..states import RefundFlow, SupportFlow
from ..storage import Repository, Ticket, User
from ..storage.models import REFUND_APPROVED, REFUND_REQUESTED

log = logging.getLogger(__name__)
router = Router(name="support")


# ════════════════════════════ поддержка ═════════════════════════════════════
@router.callback_query(F.data == "m:support")
async def cb_support(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.clear()
    await show(call, cfg.text("support_intro"), ui.support_menu(cfg))
    await call.answer()


@router.callback_query(F.data == "sup:new")
async def cb_support_new(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.set_state(SupportFlow.message)
    await state.update_data(ticket_id=0)
    await show(call, cfg.text("support_ask"), ui.cancel_only(cfg))
    await call.answer()


@router.callback_query(F.data.startswith("sup:re:"))
async def cb_support_reply(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.set_state(SupportFlow.message)
    await state.update_data(ticket_id=int(call.data.split(":")[2]))
    await show(call, cfg.text("support_ask"), ui.cancel_only(cfg))
    await call.answer()


@router.message(SupportFlow.message)
async def msg_support(message: Message, cfg: Config, repo: Repository, user: User,
                      state: FSMContext) -> None:
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer(cfg.text("support_ask"), reply_markup=ui.cancel_only(cfg))
        return
    data = await state.get_data()
    await state.clear()

    existing_id = int(data.get("ticket_id") or 0)
    ticket = await repo.get_ticket(existing_id) if existing_id else None
    if ticket and ticket.user_id == user.id:
        ticket.messages.append(f"клиент: {text[:1000]}")
        ticket.status = "open"
        await repo.save_ticket(ticket)
    else:
        ticket = await repo.create_ticket(Ticket(
            user_id=user.id, user_name=user.name, username=user.username,
            kind="support", text=text[:1000],
        ))

    await message.answer(cfg.text("support_created", ticket_id=ticket.id),
                         reply_markup=ui.main_menu(cfg, cfg.is_admin(user.id)))
    await notify_admins(
        message.bot, cfg,
        f"💬 <b>Обращение #{ticket.id}</b>\n"
        f'👤 <a href="tg://user?id={user.id}">{user.name or user.id}</a> (<code>{user.id}</code>)\n\n'
        f"{text[:2000]}",
        ui.admin_ticket_card(cfg, ticket),
    )


# ════════════════════════════ возвраты ══════════════════════════════════════
@router.callback_query(F.data == "m:refund")
async def cb_refund(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                    state: FSMContext) -> None:
    await state.clear()
    window = int(cfg.get("refund.window_hours", 72))
    policy = cfg.render(str(cfg.get("refund.policy", "")), window=window)
    orders = await repo.refundable_orders(user.id, window)
    if not orders:
        await show(call, policy + "\n\n" + cfg.text("refund_no_orders", window=window),
                   ui.main_menu(cfg, cfg.is_admin(user.id)))
        await call.answer()
        return
    await show(call, policy + "\n\n" + cfg.text("refund_pick"), ui.refund_list(cfg, orders))
    await call.answer()


@router.callback_query(F.data.startswith("rf:"))
async def cb_refund_pick(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                         state: FSMContext) -> None:
    order = await repo.get_order(int(call.data.split(":", 1)[1]))
    if not order or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await state.set_state(RefundFlow.reason)
    await state.update_data(order_id=order.id)
    await show(call, cfg.text("refund_ask_reason", order_id=order.id), ui.cancel_only(cfg))
    await call.answer()


@router.message(RefundFlow.reason)
async def msg_refund_reason(message: Message, cfg: Config, repo: Repository, user: User,
                            state: FSMContext) -> None:
    reason = (message.text or "").strip()
    if len(reason) < 5:
        await message.answer(cfg.text("refund_ask_reason", order_id=""),
                             reply_markup=ui.cancel_only(cfg))
        return
    data = await state.get_data()
    await state.clear()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order or order.user_id != user.id:
        await message.answer(cfg.text("cancelled"), reply_markup=ui.main_menu(cfg, False))
        return

    order.status = REFUND_REQUESTED
    order.reason = reason[:400]
    await repo.save_order(order, event=f"клиент запросил возврат: {reason[:200]}")
    ticket = await repo.create_ticket(Ticket(
        user_id=user.id, user_name=user.name, username=user.username,
        kind="refund", order_id=order.id, text=reason[:1000],
    ))
    await send_sticker(message.bot, cfg, message.chat.id, "refund")
    await message.answer(cfg.text("refund_created", order_id=order.id),
                         reply_markup=ui.main_menu(cfg, cfg.is_admin(user.id)))
    await notify_order(message.bot, cfg, order,
                       header=f"↩️ <b>Запрос возврата</b> · обращение #{ticket.id}")


@router.callback_query(F.data.startswith("rfw:"))
async def cb_refund_wallet(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                           state: FSMContext) -> None:
    order = await repo.get_order(int(call.data.split(":", 1)[1]))
    if not order or order.user_id != user.id or order.status != REFUND_APPROVED:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await state.set_state(RefundFlow.wallet)
    await state.update_data(order_id=order.id)
    await show(call, cfg.text("refund_approved", order_id=order.id), ui.cancel_only(cfg))
    await call.answer()


@router.message(RefundFlow.wallet)
async def msg_refund_wallet(message: Message, cfg: Config, repo: Repository, user: User,
                            state: FSMContext) -> None:
    wallet = (message.text or "").strip()
    if len(wallet) < 12 or " " in wallet:
        await message.answer("Пришли адрес кошелька одной строкой, без пробелов.",
                             reply_markup=ui.cancel_only(cfg))
        return
    data = await state.get_data()
    await state.clear()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order or order.user_id != user.id:
        await message.answer(cfg.text("cancelled"), reply_markup=ui.main_menu(cfg, False))
        return
    order.refund_wallet = wallet[:200]
    await repo.save_order(order, event=f"клиент указал кошелёк для возврата: {wallet[:80]}")
    await message.answer(cfg.text("refund_wallet_saved"),
                         reply_markup=ui.main_menu(cfg, cfg.is_admin(user.id)))
    await notify_order(message.bot, cfg, order, header="👛 <b>Кошелёк для возврата получен</b>")
