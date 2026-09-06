"""Оффлайн-прогон логики: хранилище, заказы, склад, сплит-комиссия, роутеры.

Запуск: BOT_TOKEN=1:test python -m tests.smoke
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:test")

from bot.config import Config, Settings  # noqa: E402
from bot.handlers import build_router  # noqa: E402
from bot.services.split_parser import domain_allowed, extract_url, parse_page  # noqa: E402
from bot.storage import Order, Repository, Ticket  # noqa: E402
from bot.storage.backends import LocalBackend  # noqa: E402
from bot.storage.models import AWAITING_CHECK, DONE, PAID, REFUND_REQUESTED  # noqa: E402

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(f"провал: {label}")
    print(f"  ✓ {label}")


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bot-smoke-"))
    try:
        cfg = Config.load(Settings.from_env())
        repo = Repository(LocalBackend(tmp))
        await repo.start()

        print("Пользователи")
        user = await repo.touch_user(777, "Иван Петров", "ivan")
        check((await repo.get_user(777)).username == "ivan", "пользователь сохранён")
        check(len(await repo.all_users()) == 1, "перечисление пользователей")

        print("Склад промокодов")
        await repo.stock_add("sc7", ["CODE-A", "CODE-B", "CODE-A"])
        check(await repo.stock_count("sc7") == 2, "дубликаты кодов отсеяны")
        check(await repo.stock_take("sc7") == "CODE-A", "код выдан из склада")
        check(await repo.stock_count("sc7") == 1, "код списан со склада")
        await repo.stock_return("sc7", "CODE-A")
        check(await repo.stock_count("sc7") == 2, "код возвращён на склад")

        print("Заказ на самокат")
        item = cfg.section_items("scooters")[1]
        order = await repo.create_order(Order(
            user_id=user.id, user_name=user.name, username=user.username,
            kind="scooter", product_id=str(item["id"]), title=str(item["title"]),
            amount=int(item["price"]), currency=cfg.currency,
        ))
        check(order.id > 0, "заказу присвоен номер")
        check(order.id in (await repo.get_user(777)).orders, "заказ привязан к пользователю")
        order.status = AWAITING_CHECK
        await repo.save_order(order, event="клиент сообщил об оплате")
        check(len(await repo.pending_orders()) == 1, "заказ попал в очередь проверки")
        order.status = PAID
        await repo.save_order(order, event="оплата подтверждена")
        check(len(await repo.refundable_orders(777, 72)) == 1, "возврат доступен в окне")
        check(len(await repo.refundable_orders(777, 0)) == 0, "вне окна возврат недоступен")
        order.status = DONE
        await repo.save_order(order, event="выдан доступ")
        restored = await repo.get_order(order.id)
        check(restored is not None and restored.status == DONE, "статус перечитан из хранилища")
        check(len(restored.history) >= 4, "история событий пишется")

        print("Сплит")
        check(cfg.split_fee(59_999) == 2000, "комиссия нижнего тарифа")
        check(cfg.split_fee(60_000) == 2000, "граница тарифа включительно")
        check(cfg.split_fee(60_001) == 5000, "комиссия верхнего тарифа")
        check(cfg.split_fee(150_001) is None, "выше лимита — отказ")
        check(domain_allowed("https://market.yandex.ru/p/1", cfg.get("split.allowed_domains")),
              "домен Яндекса разрешён")
        check(not domain_allowed("https://market.yandex.ru.evil.com/p", cfg.get("split.allowed_domains")),
              "поддельный домен отсечён")
        check(extract_url("смотри https://ya.cc/abc пж") == "https://ya.cc/abc", "ссылка извлечена")
        parsed = parse_page("https://market.yandex.ru/p", """
            <script type="application/ld+json">
            {"@type":"Product","name":"Пылесос","offers":{"price":"12 990"}}</script>""")
        check(parsed.price == 12990 and parsed.title == "Пылесос", "цена и название распознаны")

        split_order = await repo.create_order(Order(
            user_id=user.id, kind="split", product_id="split", title="Сплит · Пылесос",
            amount=cfg.split_fee(12990) or 0, item_price=12990,
            item_url="https://market.yandex.ru/p", delivery_title="Курьером",
            address="Москва, Тверская 1, кв 2",
        ))
        check(split_order.amount == 2000, "к оплате только комиссия сервиса")

        print("Возврат и обращения")
        split_order.status = REFUND_REQUESTED
        await repo.save_order(split_order, event="запрошен возврат")
        ticket = await repo.create_ticket(Ticket(
            user_id=user.id, kind="refund", order_id=split_order.id, text="не подошёл товар"))
        check(ticket.id > 0 and len(await repo.open_tickets()) == 1, "обращение создано")
        ticket.status = "closed"
        await repo.save_ticket(ticket)
        check(len(await repo.open_tickets()) == 0, "обращение закрывается")

        print("Скрытность админа")
        from bot import ui  # локальный импорт: нужен только здесь
        admin_view = {b.callback_data for row in ui.main_menu(cfg, True).inline_keyboard
                      for b in row if b.callback_data}
        client_view = {b.callback_data for row in ui.main_menu(cfg, False).inline_keyboard
                       for b in row if b.callback_data}
        check(cfg.stealth, "скрытый режим включён по умолчанию")
        check(admin_view == client_view, "меню админа не отличается от клиентского")
        check(not any(cb.startswith("a:") for cb in admin_view), "кнопок админки нет в меню")
        check(not (cfg.get("access.admins") or []), "ID админов не хранятся в config.yml")
        check(not cfg.get("access.admin_chat_id"), "ID чата админов не хранится в config.yml")
        check("@" not in cfg.support_contact, "личный аккаунт не подставляется в тексты")
        check("admin" not in cfg.text("start").lower() and
              "админ" not in cfg.text("menu").lower(), "меню не упоминает админку")

        print("Цветные кнопки и премиум-эмодзи (Bot API 9.4)")
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import SendMessage
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from bot.services.emoji_guard import CustomEmojiGuard

        styles = {b.style for row in ui.payment_methods(cfg, 1, True).inline_keyboard
                  for b in row}
        check(styles <= {None, "danger", "success", "primary", "link"},
              "используются только допустимые значения style")
        check("primary" in styles and "danger" in styles, "акценты расставлены")
        cfg.data["decor"]["button_styles"] = False
        off = {b.style for row in ui.payment_methods(cfg, 1, True).inline_keyboard for b in row}
        check(off == {None}, "флаг button_styles выключает цвета целиком")
        cfg.data["decor"]["button_styles"] = True

        check(not cfg.custom_emoji_on, "премиум-эмодзи выключены (нет Premium)")
        cfg.data["decor"]["custom_emoji"]["pay"] = "5285430309720966085"
        check(cfg.icon("pay") is None and "<tg-emoji" not in cfg.deco("pay"),
              "заполненный id не используется, пока флаг выключен")
        check(ui.product_card(cfg, "sc7").inline_keyboard[0][0].style == "success",
              "цвета кнопок работают и без Premium")
        cfg.data["decor"]["custom_emoji_enabled"] = True
        check("<tg-emoji" in cfg.deco("pay") and cfg.icon("pay"),
              "флаг включает премиум-эмодзи разом в текстах и на кнопках")
        cfg.data["decor"]["custom_emoji_enabled"] = False

        guard, calls = CustomEmojiGuard(), []

        async def fake_request(bot, method):
            first = method.reply_markup.inline_keyboard[0][0]
            calls.append((method.text, first.icon_custom_emoji_id, first.style))
            if len(calls) == 1:
                raise TelegramBadRequest(method=method, message="Bad Request: CUSTOM_EMOJI_INVALID")
            return "ok"

        def sample() -> SendMessage:
            return SendMessage(chat_id=1, text='Тест <tg-emoji emoji-id="1">👍</tg-emoji>',
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                   InlineKeyboardButton(text="Оплатить", callback_data="x",
                                                        style="success",
                                                        icon_custom_emoji_id="2")]]))

        await guard(fake_request, None, sample())
        check(len(calls) == 2, "битый premium-эмодзи вызывает ровно один повтор")
        check("<tg-emoji" not in calls[1][0] and calls[1][1] is None,
              "повтор уходит без кастомных эмодзи")
        check(calls[1][2] == "success", "цвет кнопки при этом сохраняется")
        await guard(fake_request, None, sample())
        check(len(calls) == 3 and calls[2][1] is None,
              "дальше эмодзи вычищаются превентивно, без лишнего запроса")

        print("Статистика и роутеры")
        stats = await repo.stats()
        check(stats["orders"] == 2 and stats["done"] == 1, "статистика считается")
        check(stats["revenue"] == int(item["price"]), "оборот по выполненным заказам")
        router = build_router()
        names = [r.name for r in router.sub_routers]
        check(len(names) == 8 and names[-1] == "fallback", f"роутеры собраны: {names}")
        await repo.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n✅ Все проверки пройдены ({checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
