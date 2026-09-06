"""Оплата: CryptoBot-счёт и ручной перевод на кошелёк + приём подтверждения."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import notify_order, show
from ..config import Config, fmt_crypto
from ..services.cryptobot import CryptoPay
from ..services.rates import Rates
from ..states import PayFlow
from ..storage import Order, Repository
from ..storage.models import AWAITING_CHECK, NEW

log = logging.getLogger(__name__)
router = Router(name="payment")


async def offer_payment(event: Message | CallbackQuery, cfg: Config, order: Order,
                        crypto: CryptoPay) -> None:
    text = cfg.text(
        "order_created",
        order_id=order.id,
        title=order.title,
        amount=cfg.money(order.amount),
        ttl=cfg.get("payment.order_ttl_minutes", 120),
    )
    await show(event, text, ui.payment_methods(cfg, order.id, crypto.enabled))


async def _load(call: CallbackQuery, repo: Repository, order_id: int) -> Order | None:
    order = await repo.get_order(order_id)
    if not order or order.user_id != call.from_user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return None
    return order


# ════════════════════════ выбор способа ═════════════════════════════════════
@router.callback_query(F.data.startswith("pm:"))
async def cb_method(call: CallbackQuery, cfg: Config, repo: Repository, crypto: CryptoPay,
                    rates: Rates, state: FSMContext) -> None:
    _, raw_id, method = call.data.split(":", 2)
    order = await _load(call, repo, int(raw_id))
    if order is None:
        return
    if order.status not in (NEW,):
        await call.answer("Заказ уже оплачен или закрыт", show_alert=True)
        return

    if method == "back":
        await offer_payment(call, cfg, order, crypto)
        await call.answer()
        return

    if method == "cryptobot":
        if not crypto.enabled:
            await call.answer("CryptoBot не подключён", show_alert=True)
            return
        await call.answer("Создаю счёт…")
        invoice = await crypto.create_invoice(
            order.amount,
            fiat=str(cfg.get("brand.currency_code", "RUB")),
            asset=str(cfg.get("payment.cryptobot.asset", "USDT")),
            in_fiat=bool(cfg.get("payment.cryptobot.invoice_in_fiat", True)),
            description=f"Заказ #{order.id} · {order.title}",
            payload=str(order.id),
            expires_in=int(cfg.get("payment.cryptobot.expires_in", 3600)),
        )
        if not invoice:
            await call.message.answer("Не удалось создать счёт. Попробуй перевод на кошелёк.")
            return
        order.method = "cryptobot"
        order.invoice_id = invoice["invoice_id"]
        order.invoice_url = invoice["url"]
        order.wallet_title = "CryptoBot"
        await repo.save_order(order, event="выставлен счёт CryptoBot")
        await show(call, cfg.text("pay_cryptobot", order_id=order.id,
                                  amount=cfg.money(order.amount)),
                   ui.invoice_keyboard(cfg, order))
        return

    if method == "manual":
        await show(call, cfg.text("pay_choose_wallet"), ui.wallets(cfg, order.id))
        await call.answer()


@router.callback_query(F.data.startswith("pw:"))
async def cb_wallet(call: CallbackQuery, cfg: Config, repo: Repository, rates: Rates) -> None:
    _, raw_id, raw_idx = call.data.split(":", 2)
    order = await _load(call, repo, int(raw_id))
    if order is None:
        return
    wallets = cfg.get("payment.manual.wallets", []) or []
    idx = int(raw_idx)
    if idx >= len(wallets):
        await call.answer("Кошелёк не найден", show_alert=True)
        return
    wallet = wallets[idx]
    asset = str(wallet.get("asset", "USDT"))
    amount = await rates.convert(order.amount, asset)
    order.method = "manual"
    order.wallet_title = str(wallet.get("title", asset))
    order.wallet_address = str(wallet.get("address", ""))
    await repo.save_order(order, event=f"выбран перевод: {order.wallet_title}")

    await show(call, cfg.text(
        "pay_manual",
        order_id=order.id,
        wallet_title=order.wallet_title,
        amount=cfg.money(order.amount),
        crypto_amount=fmt_crypto(amount, asset) if amount else "по курсу",
        address=order.wallet_address,
        note=wallet.get("note", ""),
    ), ui.manual_keyboard(cfg, order.id))
    await call.answer()


# ════════════════════════ подтверждение оплаты ══════════════════════════════
@router.callback_query(F.data.startswith("paid:"))
async def cb_paid(call: CallbackQuery, cfg: Config, repo: Repository, state: FSMContext) -> None:
    order = await _load(call, repo, int(call.data.split(":", 1)[1]))
    if order is None:
        return
    if not cfg.get("payment.manual.require_proof", True):
        await _submit(call.message, cfg, repo, order)
        await call.answer()
        return
    await state.set_state(PayFlow.proof)
    await state.update_data(order_id=order.id)
    await show(call, cfg.text("pay_ask_proof", order_id=order.id), ui.cancel_only(cfg))
    await call.answer()


@router.message(PayFlow.proof)
async def msg_proof(message: Message, cfg: Config, repo: Repository, state: FSMContext) -> None:
    data = await state.get_data()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order or order.user_id != message.from_user.id:
        await state.clear()
        await message.answer(cfg.text("cancelled"), reply_markup=ui.main_menu(cfg, False))
        return
    if message.photo:
        order.proof_file_id = message.photo[-1].file_id
        order.proof = (message.caption or "скриншот").strip()[:200]
    elif message.text:
        order.proof = message.text.strip()[:200]
    else:
        await message.answer(cfg.text("pay_ask_proof", order_id=order.id))
        return
    await state.clear()
    await _submit(message, cfg, repo, order)


async def _submit(message: Message, cfg: Config, repo: Repository, order: Order) -> None:
    order.status = AWAITING_CHECK
    await repo.save_order(order, event="клиент сообщил об оплате")
    await message.answer(cfg.text("pay_proof_saved", order_id=order.id),
                         reply_markup=ui.order_open(cfg, order.id))
    await notify_order(message.bot, cfg, order, header="💰 <b>Новая оплата на проверку</b>")


@router.callback_query(F.data.startswith("chk:"))
async def cb_check(call: CallbackQuery, cfg: Config, repo: Repository, crypto: CryptoPay) -> None:
    order = await _load(call, repo, int(call.data.split(":", 1)[1]))
    if order is None:
        return
    if not order.invoice_id:
        await call.answer("Счёт не найден", show_alert=True)
        return
    status = await crypto.invoice_status(order.invoice_id)
    if status != "paid":
        await call.answer(cfg.text("pay_check_pending").replace("<b>", "").replace("</b>", ""),
                          show_alert=True)
        return
    order.proof = f"CryptoBot invoice {order.invoice_id}"
    order.status = AWAITING_CHECK
    await repo.save_order(order, event="счёт CryptoBot оплачен")
    await show(call, cfg.text("pay_check_paid", order_id=order.id),
               ui.order_open(cfg, order.id))
    await call.answer("Оплата получена")
    await notify_order(call.bot, cfg, order, header="💰 <b>Оплата CryptoBot на проверку</b>")
