"""Разблокировка самоката прямо в боте.

Клиент с активной подпиской заходит в «Самокаты», вводит номер самоката
(#5849, 89A78 и т.п.) и получает ответ, заданный в config.yml. Без активной
подписки раздел не пускает дальше.

Подписка считается активной, если у клиента есть оплаченный заказ на тариф
и с момента подтверждения оплаты прошло меньше duration_days.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import notify_admins, send_sticker, show
from ..config import Config
from ..states import Activation
from ..storage import Order, Repository, User

log = logging.getLogger(__name__)
router = Router(name="activation")

# «#5849», «89A78», «AB-1234» — цифры обязательны, длина ограничена конфигом
NUMBER_RE = re.compile(r"^[#№]?[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9 \-]{1,14}$")


def normalize_number(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "").strip()).upper()


def looks_like_number(cfg: Config, raw: str) -> bool:
    number = normalize_number(raw)
    low = int(cfg.get("scooters.unlock.min_len", 3))
    high = int(cfg.get("scooters.unlock.max_len", 12))
    if not low <= len(number.lstrip("#№")) <= high:
        return False
    if not any(ch.isdigit() for ch in number):
        return False
    return bool(NUMBER_RE.match(number))


def instructions_for(cfg: Config, order: Order) -> str:
    """Текст, который клиент получает после подтверждения оплаты."""
    _, item = cfg.find_product(order.product_id)
    if item and item.get("instructions"):
        return cfg.render(
            str(item["instructions"]),
            duration=item.get("duration_days", ""),
            title=item.get("title", ""),
            price=cfg.money(item.get("price", 0)),
            order_id=order.id,
        )
    if order.kind == "split":
        return cfg.render(str(cfg.get("split.instructions", "")),
                          order_id=order.id, title=order.item_title,
                          price=cfg.money(order.item_price))
    return ""


@router.callback_query(F.data == "sc:unlock")
async def cb_unlock(call: CallbackQuery, cfg: Config, repo: Repository, user: User,
                    state: FSMContext) -> None:
    """Кнопка «Разблокировать самокат» в разделе «Самокаты»."""
    subscription = await repo.active_subscription(user.id, cfg)
    if subscription is None:
        await show(call, cfg.text("unlock_no_subscription"), ui.catalog(cfg, "scooters"))
        await call.answer("Нужна активная подписка", show_alert=True)
        return

    left = repo.subscription_days_left(subscription, cfg)
    await state.set_state(Activation.data)
    await state.update_data(order_id=subscription.id)
    await show(call, cfg.text("unlock_prompt", order_id=subscription.id, days=left),
               ui.cancel_only(cfg))
    await call.answer()


@router.message(Activation.data)
async def msg_unlock(message: Message, cfg: Config, repo: Repository, user: User,
                     state: FSMContext) -> None:
    subscription = await repo.active_subscription(user.id, cfg)
    if subscription is None:                       # подписка истекла, пока вводил
        await state.clear()
        await message.answer(cfg.text("unlock_no_subscription"),
                             reply_markup=ui.catalog(cfg, "scooters"))
        return

    raw = (message.text or "").strip()
    if not looks_like_number(cfg, raw):
        await message.answer(cfg.text("unlock_bad_number"), reply_markup=ui.cancel_only(cfg))
        return

    number = normalize_number(raw)
    await state.clear()
    subscription.activation_text = number
    await repo.save_order(subscription, event=f"попытка разблокировки: {number}")

    log.info("Разблокировка %s пользователем %s (заказ #%s)", number, user.id, subscription.id)
    await send_sticker(message.bot, cfg, message.chat.id, "success")
    await message.answer(
        cfg.text("unlock_response", number=number, order_id=subscription.id),
        reply_markup=ui.unlock_again(cfg),
    )
    await notify_admins(
        message.bot, cfg,
        f"🛴 <b>Разблокировка · заказ #{subscription.id}</b>\n"
        f'👤 <a href="tg://user?id={user.id}">{user.name or user.id}</a> '
        f"(<code>{user.id}</code>)\n"
        f"🔢 номер: <code>{number}</code>",
        ui.admin_order_card(cfg, subscription),
    )
