"""Сверка кнопок и хендлеров: каждая callback_data должна кем-то обрабатываться.

Ловит опечатки вида «кнопка шлёт a:stock:sc7, а хендлер ждёт a:stk:».
Запуск: BOT_TOKEN=1:test python -m tests.check_callbacks
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:test")

from bot import ui  # noqa: E402
from bot.config import Config, Settings  # noqa: E402
from bot.storage import Order, Ticket  # noqa: E402
from bot.storage.models import (  # noqa: E402
    ACTIVATION, AWAITING_CHECK, DONE, NEW, PAID, REFUND_APPROVED, REFUND_REQUESTED,
)

EQ = re.compile(r"""F\.data\s*==\s*["']([^"']+)["']""")
STARTS = re.compile(r"""F\.data\.startswith\(\s*["']([^"']+)["']""")
IN_SET = re.compile(r"""F\.data\.in_\(\{([^}]+)\}\)""")


def handled() -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    prefixes: set[str] = set()
    for path in (ROOT / "bot" / "handlers").glob("*.py"):
        src = path.read_text("utf-8")
        exact |= set(EQ.findall(src))
        prefixes |= set(STARTS.findall(src))
        for group in IN_SET.findall(src):
            exact |= {m.strip("\"' ") for m in group.split(",") if m.strip()}
    return exact, prefixes


def buttons(cfg: Config) -> set[str]:
    order = Order(id=42, user_id=1, title="Тест", amount=2000, item_url="https://x/y",
                  invoice_url="https://t.me/CryptoBot?start=x", code="C-1")
    ticket = Ticket(id=7, user_id=1, kind="support")
    markups = [
        ui.main_menu(cfg, True), ui.main_menu(cfg, False),
        ui.catalog(cfg, "scooters"), ui.catalog(cfg, "goods"),
        ui.about_menu(cfg), ui.support_menu(cfg), ui.cancel_only(cfg),
        ui.payment_methods(cfg, order.id, True), ui.wallets(cfg, order.id),
        ui.invoice_keyboard(cfg, order), ui.manual_keyboard(cfg, order.id),
        ui.unlock_now(cfg), ui.unlock_again(cfg), ui.order_open(cfg, order.id),
        ui.catalog(cfg, "scooters", has_subscription=True),
        ui.split_start(cfg), ui.split_confirm(cfg), ui.split_delivery(cfg),
        ui.orders_list(cfg, [order]), ui.refund_list(cfg, [order]),
        ui.refund_wallet_prompt(cfg, order.id), ui.ticket_reply_prompt(cfg, ticket.id),
        ui.admin_menu(cfg), ui.admin_orders(cfg, [order]),
        ui.admin_stock(cfg, {"sc7": 3}, ["sc3", "sc7", "sc30"]),
        ui.admin_tickets(cfg, [ticket]), ui.admin_ticket_card(cfg, ticket),
        ui.confirm_broadcast(cfg),
    ]
    for product in cfg.section_items("scooters"):
        markups.append(ui.product_card(cfg, str(product["id"])))
    for status in (NEW, AWAITING_CHECK, PAID, ACTIVATION, DONE,
                   REFUND_REQUESTED, REFUND_APPROVED):
        order.status = status
        markups.append(ui.order_card(cfg, order, 72))
        markups.append(ui.admin_order_card(cfg, order))

    data: set[str] = set()
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    data.add(button.callback_data)
                if button.callback_data and len(button.callback_data.encode()) > 64:
                    raise AssertionError(f"callback_data длиннее 64 байт: {button.callback_data}")
                if button.style not in (None, "danger", "success", "primary", "link"):
                    raise AssertionError(f"недопустимый style «{button.style}» "
                                         f"у кнопки «{button.text}»")
    return data


def main() -> int:
    cfg = Config.load(Settings.from_env())
    exact, prefixes = handled()
    orphans = [
        cb for cb in sorted(buttons(cfg))
        if cb not in exact and not any(cb.startswith(p) for p in prefixes)
    ]
    if orphans:
        print("❌ Кнопки без обработчика:")
        for cb in orphans:
            print("  •", cb)
        return 1
    print(f"✅ Все кнопки обрабатываются "
          f"(точных правил: {len(exact)}, префиксных: {len(prefixes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
