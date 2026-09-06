"""Активация после подтверждения оплаты.

Клиент присылает номер самоката или скриншот — в ответ уходит текст,
который задан в config.yml (instructions у товара или split.instructions).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import notify_order, send_sticker, show
from ..config import Config
from ..states import Activation
from ..storage import Repository, User
from ..storage.models import ACTIVATION, DONE, PAID

log = logging.getLogger(__name__)
router = Router(name="activation")


def instructions_for(cfg: Config, order) -> str:
    _, item = cfg.find_product(order.product_id)
    if item and item.get("instructions"):
        return cfg.render(str(item["instructions"]))
    if order.kind == "split":
        return cfg.render(str(cfg.get("split.instructions", "")))
    return ""


@router.callback_query(F.data.startswith("act:"))
async def cb_activation(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                        state: FSMContext) -> None:
    _, raw_id, mode = call.data.split(":", 2)
    order = await repo.get_order(int(raw_id))
    if not order or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status not in (PAID, ACTIVATION):
        await call.answer("Активация доступна после подтверждения оплаты", show_alert=True)
        return

    if mode == "menu":
        await show(call, cfg.text("activation_menu", order_id=order.id),
                   ui.activation_menu(cfg, order.id))
        await call.answer()
        return

    await state.set_state(Activation.data)
    await state.update_data(order_id=order.id, mode=mode)
    prompt = "activation_ask_number" if mode == "num" else "activation_ask_photo"
    await show(call, cfg.text(prompt), ui.cancel_only(cfg))
    await call.answer()


@router.message(Activation.data)
async def msg_activation(message: Message, cfg: Config, repo: Repository, user: User,
                         state: FSMContext) -> None:
    data = await state.get_data()
    order = await repo.get_order(int(data.get("order_id", 0)))
    if not order or order.user_id != user.id:
        await state.clear()
        await message.answer(cfg.text("cancelled"), reply_markup=ui.main_menu(cfg, False))
        return

    if message.photo:
        order.activation_file_id = message.photo[-1].file_id
        order.activation_text = (message.caption or "скриншот от клиента").strip()[:200]
    elif message.text and message.text.strip():
        order.activation_text = message.text.strip()[:200]
    else:
        await message.answer(cfg.text("activation_bad_input"))
        return

    await state.clear()
    order.status = DONE
    await repo.save_order(order, event=f"клиент прислал данные: {order.activation_text}")

    instructions = instructions_for(cfg, order)
    await send_sticker(message.bot, cfg, message.chat.id, "success")
    await message.answer(
        cfg.text("activation_done", order_id=order.id, instructions=instructions),
        reply_markup=ui.order_open(cfg, order.id),
        disable_web_page_preview=True,
    )
    if order.code:
        await message.answer(cfg.text("activation_code", code=order.code))

    await notify_order(message.bot, cfg, order, header="🛴 <b>Клиент прислал данные активации</b>")
