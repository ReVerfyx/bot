"""Админ-панель: проверка оплат, склад кодов, обращения, возвраты, рассылка."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from .. import ui
from ..common import admin_order_text, confirm_payment, safe_send, show
from ..config import Config
from ..states import AdminFlow
from ..storage import Repository
from ..storage.models import (
    AWAITING_CHECK, NEW, REFUND_APPROVED, REFUND_REQUESTED, REFUNDED, REJECTED,
)

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, is_admin: bool = False) -> bool:
        return bool(is_admin)


class SecretPhrase(BaseFilter):
    """Кодовое слово из ADMIN_SECRET — тихий вход без команды в интерфейсе."""

    async def __call__(self, message: Message, cfg: Config) -> bool:
        secret = cfg.settings.admin_secret
        return bool(secret) and (message.text or "").strip() == secret


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _menu(event: Message | CallbackQuery, cfg: Config, repo: Repository) -> None:
    pending = await repo.pending_orders()
    tickets = await repo.open_tickets()
    await show(event, cfg.text("admin_menu", pending=len(pending), tickets=len(tickets)),
               ui.admin_menu(cfg))


@router.message(StateFilter(None), SecretPhrase())
async def secret_entry(message: Message, cfg: Config, repo: Repository,
                       state: FSMContext) -> None:
    """Открыть панель по кодовому слову и стереть само слово из переписки."""
    await state.clear()
    with contextlib.suppress(TelegramBadRequest):
        await message.delete()
    await _menu(message, cfg, repo)


@router.message(Command("admin"))
async def cmd_admin(message: Message, cfg: Config, repo: Repository, state: FSMContext) -> None:
    await state.clear()
    if cfg.stealth:                               # не оставляем следа команды в чате
        with contextlib.suppress(TelegramBadRequest):
            await message.delete()
    await _menu(message, cfg, repo)


@router.callback_query(F.data == "a:menu")
async def cb_menu(call: CallbackQuery, cfg: Config, repo: Repository, state: FSMContext) -> None:
    await state.clear()
    await _menu(call, cfg, repo)
    await call.answer()


# ════════════════════════════ заказы ════════════════════════════════════════
@router.callback_query(F.data.in_({"a:pending", "a:orders"}))
async def cb_orders(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    if call.data == "a:pending":
        orders = await repo.pending_orders()
        if not orders:
            await show(call, cfg.text("admin_no_pending"), ui.admin_menu(cfg))
            await call.answer()
            return
        header = f"📥 <b>На проверке: {len(orders)}</b>"
    else:
        orders = await repo.list_orders(limit=30)
        header = f"📋 <b>Последние заказы: {len(orders)}</b>"
    await show(call, header, ui.admin_orders(cfg, orders))
    await call.answer()


@router.callback_query(F.data.startswith("a:ord:"))
async def cb_order(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    order = await repo.get_order(int(call.data.split(":")[2]))
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await show(call, admin_order_text(cfg, order), ui.admin_order_card(cfg, order))
    await call.answer()


@router.callback_query(F.data.startswith("a:ok:"))
async def cb_confirm(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    order = await repo.get_order(int(call.data.split(":")[2]))
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status not in (AWAITING_CHECK, NEW):
        await call.answer("Заказ уже обработан", show_alert=True)
        return
    await call.answer("Подтверждаю…")
    order = await confirm_payment(call.bot, cfg, repo, order)
    await show(call, admin_order_text(cfg, order), ui.admin_order_card(cfg, order))


@router.callback_query(F.data.startswith("a:no:"))
async def cb_reject(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    order_id = int(call.data.split(":")[2])
    await state.set_state(AdminFlow.reject_reason)
    await state.update_data(order_id=order_id)
    await show(call, cfg.text("admin_ask_reason", order_id=order_id), ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.reject_reason)
async def msg_reject(message: Message, cfg: Config, repo: Repository, state: FSMContext) -> None:
    reason = (message.text or "без причины").strip()[:400]
    data = await state.get_data()
    await state.clear()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order:
        await message.answer("Заказ не найден")
        return
    _, item = cfg.find_product(order.product_id)
    if order.code and item and item.get("sku"):
        await repo.stock_return(str(item["sku"]), order.code)
        order.code = ""
    order.status = REJECTED
    order.reason = reason
    await repo.save_order(order, event=f"отклонён оператором: {reason}")
    await safe_send(message.bot, order.user_id,
                    cfg.text("order_rejected", order_id=order.id, reason=reason))
    await message.answer(f"❌ Заказ #{order.id} отклонён.", reply_markup=ui.admin_menu(cfg))


@router.callback_query(F.data.startswith("a:msg:"))
async def cb_message_user(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    order_id = int(call.data.split(":")[2])
    await state.set_state(AdminFlow.message_user)
    await state.update_data(order_id=order_id)
    await show(call, f"Напиши сообщение клиенту по заказу <b>#{order_id}</b>:",
               ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.message_user)
async def msg_to_user(message: Message, cfg: Config, repo: Repository, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order:
        await message.answer("Заказ не найден")
        return
    text = (message.text or "").strip()
    ok = await safe_send(
        message.bot, order.user_id,
        f"{cfg.emoji('support')} <b>Сообщение оператора по заказу #{order.id}</b>\n\n{text}",
        ui.order_open(cfg, order.id),
    )
    await repo.save_order(order, event=f"оператор написал клиенту: {text[:150]}")
    await message.answer("✅ Отправлено." if ok else "⚠️ Клиент недоступен.",
                         reply_markup=ui.admin_menu(cfg))


# ════════════════════════════ возвраты ══════════════════════════════════════
@router.callback_query(F.data.startswith("a:rfok:"))
async def cb_refund_ok(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    order = await repo.get_order(int(call.data.split(":")[2]))
    if not order or order.status != REFUND_REQUESTED:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    order.status = REFUND_APPROVED
    await repo.save_order(order, event="возврат одобрен оператором")
    await safe_send(call.bot, order.user_id, cfg.text("refund_approved", order_id=order.id),
                    ui.refund_wallet_prompt(cfg, order.id))
    await show(call, admin_order_text(cfg, order), ui.admin_order_card(cfg, order))
    await call.answer("Возврат одобрен")


@router.callback_query(F.data.startswith("a:rfno:"))
async def cb_refund_no(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    order_id = int(call.data.split(":")[2])
    await state.set_state(AdminFlow.refund_reason)
    await state.update_data(order_id=order_id)
    await show(call, f"Причина отказа в возврате по заказу <b>#{order_id}</b>:",
               ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.refund_reason)
async def msg_refund_no(message: Message, cfg: Config, repo: Repository,
                        state: FSMContext) -> None:
    reason = (message.text or "без причины").strip()[:400]
    data = await state.get_data()
    await state.clear()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order:
        await message.answer("Заказ не найден")
        return
    order.status = "done" if order.code or order.activation_text else "paid"
    order.reason = reason
    await repo.save_order(order, event=f"в возврате отказано: {reason}")
    await safe_send(message.bot, order.user_id,
                    cfg.text("refund_declined", order_id=order.id, reason=reason))
    await message.answer("Отказ отправлен клиенту.", reply_markup=ui.admin_menu(cfg))


@router.callback_query(F.data.startswith("a:rfdone:"))
async def cb_refund_done(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    order = await repo.get_order(int(call.data.split(":")[2]))
    if not order or order.status != REFUND_APPROVED:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    order.status = REFUNDED
    await repo.save_order(order, event="средства возвращены")
    await safe_send(call.bot, order.user_id, cfg.text("refund_done", order_id=order.id))
    await show(call, admin_order_text(cfg, order), ui.admin_order_card(cfg, order))
    await call.answer("Отмечено как возвращённое")


# ════════════════════════════ склад кодов ═══════════════════════════════════
def _skus(cfg: Config) -> list[str]:
    skus = []
    for section in ("scooters", "goods"):
        for item in cfg.section_items(section):
            sku = str(item.get("sku") or "")
            if sku and sku not in skus:
                skus.append(sku)
    return skus


@router.callback_query(F.data == "a:stock")
async def cb_stock(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    counts = await repo.stock_all()
    skus = _skus(cfg)
    lines = [f"• <code>{sku}</code> — <b>{counts.get(sku, 0)}</b> шт" for sku in skus] or ["—"]
    await show(call, "📦 <b>Склад промокодов</b>\n\n" + "\n".join(lines),
               ui.admin_stock(cfg, counts, skus))
    await call.answer()


@router.callback_query(F.data.startswith("a:stk:"))
async def cb_stock_add(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    sku = call.data.split(":", 2)[2]
    await state.set_state(AdminFlow.codes)
    await state.update_data(sku=sku)
    await show(call, cfg.text("admin_ask_codes", sku=sku), ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.codes)
async def msg_stock_add(message: Message, cfg: Config, repo: Repository,
                        state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    sku = str(data.get("sku", ""))
    codes = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    added = await repo.stock_add(sku, codes)
    await message.answer(cfg.text("admin_codes_added", count=added, sku=sku),
                         reply_markup=ui.admin_menu(cfg))


# ════════════════════════════ обращения ═════════════════════════════════════
@router.callback_query(F.data == "a:tickets")
async def cb_tickets(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    tickets = await repo.open_tickets()
    if not tickets:
        await show(call, "🎫 Открытых обращений нет.", ui.admin_menu(cfg))
        await call.answer()
        return
    await show(call, f"🎫 <b>Открытых обращений: {len(tickets)}</b>", ui.admin_tickets(cfg, tickets))
    await call.answer()


@router.callback_query(F.data.startswith("a:tk:"))
async def cb_ticket(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    ticket = await repo.get_ticket(int(call.data.split(":")[2]))
    if not ticket:
        await call.answer("Обращение не найдено", show_alert=True)
        return
    body = "\n".join(f"• {m}" for m in ticket.messages[-10:])
    text = (f"🎫 <b>Обращение #{ticket.id}</b> · {ticket.kind} · {ticket.status}\n"
            f'👤 <a href="tg://user?id={ticket.user_id}">{ticket.user_name or ticket.user_id}</a>'
            f" (<code>{ticket.user_id}</code>)\n\n{body}")
    await show(call, text, ui.admin_ticket_card(cfg, ticket))
    await call.answer()


@router.callback_query(F.data.startswith("a:tkr:"))
async def cb_ticket_reply(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    ticket_id = int(call.data.split(":")[2])
    await state.set_state(AdminFlow.reply_ticket)
    await state.update_data(ticket_id=ticket_id)
    await show(call, cfg.text("admin_ask_reply", ticket_id=ticket_id), ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.reply_ticket)
async def msg_ticket_reply(message: Message, cfg: Config, repo: Repository,
                           state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    ticket = await repo.get_ticket(int(data.get("ticket_id", 0)))
    if not ticket:
        await message.answer("Обращение не найдено")
        return
    text = (message.text or "").strip()
    ticket.messages.append(f"оператор: {text[:1000]}")
    await repo.save_ticket(ticket)
    await safe_send(message.bot, ticket.user_id,
                    cfg.text("support_reply", ticket_id=ticket.id, text=text),
                    ui.ticket_reply_prompt(cfg, ticket.id))
    await message.answer(cfg.text("admin_reply_sent"), reply_markup=ui.admin_menu(cfg))


@router.callback_query(F.data.startswith("a:tkc:"))
async def cb_ticket_close(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    ticket = await repo.get_ticket(int(call.data.split(":")[2]))
    if not ticket:
        await call.answer("Обращение не найдено", show_alert=True)
        return
    ticket.status = "closed"
    await repo.save_ticket(ticket)
    await cb_tickets(call, cfg, repo)           # answer() сделает он


# ════════════════════════════ статистика и рассылка ═════════════════════════
@router.callback_query(F.data == "a:stats")
async def cb_stats(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    stats = await repo.stats()
    stock = ", ".join(f"{k}:{v}" for k, v in stats["stock"].items()) or "пусто"
    await show(call, cfg.text("admin_stats", users=stats["users"], orders=stats["orders"],
                              done=stats["done"], pending=stats["pending"],
                              revenue=cfg.money(stats["revenue"]), stock=stock),
               ui.admin_menu(cfg))
    await call.answer()


@router.callback_query(F.data == "a:cast")
async def cb_cast(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.set_state(AdminFlow.broadcast)
    await show(call, cfg.text("admin_ask_broadcast"), ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.broadcast)
async def msg_cast(message: Message, cfg: Config, state: FSMContext) -> None:
    text = (message.text or message.html_text or "").strip()
    if not text:
        await message.answer("Нужен текст.")
        return
    await state.update_data(text=text)
    await message.answer(f"Предпросмотр:\n\n{text}", reply_markup=ui.confirm_broadcast(cfg))


@router.callback_query(F.data == "a:cast:go")
async def cb_cast_go(call: CallbackQuery, cfg: Config, repo: Repository,
                     state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    text = str(data.get("text", "")).strip()
    if not text:
        await call.answer("Текст потерялся, начни заново", show_alert=True)
        return
    await call.answer("Рассылаю…")
    ok = fail = 0
    for user in await repo.all_users():
        if user.banned:
            continue
        if await safe_send(call.bot, user.id, text):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(0.05)                  # ~20 сообщений в секунду
    await call.message.answer(cfg.text("admin_broadcast_done", ok=ok, fail=fail),
                              reply_markup=ui.admin_menu(cfg))


@router.callback_query(F.data == "a:ban")
async def cb_ban(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.set_state(AdminFlow.ban)
    await show(call, cfg.text("admin_ask_ban"), ui.cancel_only(cfg))
    await call.answer()


@router.message(AdminFlow.ban)
async def msg_ban(message: Message, cfg: Config, repo: Repository, state: FSMContext) -> None:
    await state.clear()
    raw = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if not raw:
        await message.answer("Нужен числовой ID.", reply_markup=ui.admin_menu(cfg))
        return
    user_id = int(raw)
    current = await repo.get_user(user_id)
    banned = not (current.banned if current else False)
    await repo.set_ban(user_id, banned)
    await message.answer(cfg.text("admin_ban_done", user_id=user_id,
                                  state="забанен 🚫" if banned else "разбанен ✅"),
                         reply_markup=ui.admin_menu(cfg))
