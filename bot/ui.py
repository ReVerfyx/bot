"""Клавиатуры и мелкая визуальная обвязка.

Цвета кнопок и иконки-премиум-эмодзи появились в Bot API 9.4: поля style
(danger / success / primary) и icon_custom_emoji_id. Клиенты постарше просто
покажут обычную кнопку, поэтому дополнительной деградации не требуется.

Правило расстановки цветов: на экране не больше одного акцента. Зелёный —
шаг, продвигающий заказ вперёд (оплатить, подтвердить), красный —
разрушающее действие (отменить, отклонить, бан), синий — основная навигация.
Всё остальное остаётся нейтральным, иначе акценты перестают читаться.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config
from .storage.models import Order

Row = list[InlineKeyboardButton]


def btn(text: str, data: str, style: str | None = None,
        icon: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style,
                                icon_custom_emoji_id=icon)


def url_btn(text: str, url: str, style: str | None = None,
            icon: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url, style=style,
                                icon_custom_emoji_id=icon)


def kb(*rows: Row) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def back(cfg: Config, target: str = "m:main", text: str = "В меню") -> Row:
    return [btn(f"{cfg.emoji('back')} {text}", target)]


# ════════════════════════════ меню ══════════════════════════════════════════
def main_menu(cfg: Config, is_admin: bool = False) -> InlineKeyboardMarkup:
    e = cfg.emoji
    rows: list[Row] = []
    if cfg.section_items("scooters"):
        rows.append([btn(f"{e('scooter')} Самокаты Яндекс Go", "m:scooters",
                         cfg.style("primary"), cfg.icon("scooter"))])
    second: Row = []
    if cfg.get("split.enabled", True):
        second.append(btn(f"{e('split')} Выкуп по Сплиту", "m:split",
                          icon=cfg.icon("split")))
    if cfg.get("goods.enabled", True):
        second.append(btn(f"{e('bag')} Товары с Яндекса", "m:goods",
                          icon=cfg.icon("bag")))
    rows.append(second)
    rows.append([btn(f"{e('orders')} Мои заказы", "m:orders"),
                 btn(f"{e('support')} Поддержка", "m:support")])
    tail: Row = []
    if cfg.get("refund.enabled", True):
        tail.append(btn(f"{e('refund')} Возврат средств", "m:refund"))
    tail.append(btn(f"{e('info')} О сервисе", "m:about"))
    rows.append(tail)
    # В скрытом режиме кнопки админки нет ни у кого: вход только по /admin
    # или кодовому слову, чтобы по интерфейсу нельзя было вычислить оператора.
    if is_admin and not cfg.stealth:
        rows.append([btn(f"{e('admin')} Админ-панель", "a:menu")])
    return kb(*rows)


def catalog(cfg: Config, section: str) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    for item in cfg.section_items(section):
        emoji = item.get("emoji") or cfg.emoji("bag")
        badge = f" · {item['badge']}" if item.get("badge") else ""
        rows.append([btn(f"{emoji} {item.get('title')}{badge} — {cfg.money(item.get('price', 0))}",
                         f"p:{item.get('id')}")])
    if section == "goods" and cfg.get("split.enabled", True):
        rows.append([btn(f"{cfg.emoji('split')} Нужного нет — выкуп по ссылке", "m:split")])
    rows.append(back(cfg))
    return kb(*rows)


def product_card(cfg: Config, product_id: str, in_stock: bool = True) -> InlineKeyboardMarkup:
    section, item = cfg.find_product(product_id)
    buy: Row = [btn(f"{cfg.emoji('pay')} Оформить заказ", f"buy:{product_id}",
                    cfg.style("success"), cfg.icon("pay"))] if in_stock else \
               [btn(f"{cfg.emoji('clock')} Нет в наличии", "nop")]
    return kb(buy, back(cfg, f"m:{section or 'scooters'}", "Назад"))


def about_menu(cfg: Config) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    links: Row = []
    if cfg.get("brand.channel_url"):
        links.append(url_btn("📣 Канал", str(cfg.get("brand.channel_url"))))
    if cfg.get("brand.backup_url"):
        links.append(url_btn("🔁 Переходник", str(cfg.get("brand.backup_url"))))
    if links:
        rows.append(links)
    if cfg.get("brand.reviews_url"):
        rows.append([url_btn("⭐️ Отзывы", str(cfg.get("brand.reviews_url")))])
    rows.append(back(cfg))
    return kb(*rows)


# ════════════════════════════ оплата ════════════════════════════════════════
def payment_methods(cfg: Config, order_id: int, cryptobot: bool) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    if cryptobot and cfg.get("payment.cryptobot.enabled", True):
        rows.append([btn(f"{cfg.emoji('crypto')} Оплатить через CryptoBot",
                         f"pm:{order_id}:cryptobot", cfg.style("primary"),
                         cfg.icon("crypto"))])
    if cfg.get("payment.manual.enabled", True):
        rows.append([btn(f"{cfg.emoji('wallet')} Перевод на кошелёк",
                         f"pm:{order_id}:manual", icon=cfg.icon("wallet"))])
    rows.append([btn(f"{cfg.emoji('cross')} Отменить заказ", f"cancel:{order_id}",
                     cfg.style("danger"), cfg.icon("cross"))])
    rows.append(back(cfg))
    return kb(*rows)


def wallets(cfg: Config, order_id: int) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    for idx, wallet in enumerate(cfg.get("payment.manual.wallets", []) or []):
        rows.append([btn(f"{cfg.emoji('crypto')} {wallet.get('title')}", f"pw:{order_id}:{idx}")])
    rows.append([btn(f"{cfg.emoji('back')} Назад", f"pm:{order_id}:back")])
    return kb(*rows)


def invoice_keyboard(cfg: Config, order: Order) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    if order.invoice_url:
        rows.append([url_btn(f"{cfg.emoji('pay')} Оплатить", order.invoice_url,
                             cfg.style("success"), cfg.icon("pay"))])
        rows.append([btn(f"{cfg.emoji('check')} Проверить оплату", f"chk:{order.id}",
                         cfg.style("primary"), cfg.icon("check"))])
    rows.append([btn(f"{cfg.emoji('cross')} Отменить", f"cancel:{order.id}",
                     cfg.style("danger"), cfg.icon("cross"))])
    rows.append(back(cfg))
    return kb(*rows)


def manual_keyboard(cfg: Config, order_id: int) -> InlineKeyboardMarkup:
    return kb(
        [btn(f"{cfg.emoji('check')} Я оплатил", f"paid:{order_id}",
             cfg.style("success"), cfg.icon("check"))],
        [btn(f"{cfg.emoji('back')} Другая монета", f"pm:{order_id}:manual")],
        [btn(f"{cfg.emoji('cross')} Отменить", f"cancel:{order_id}",
             cfg.style("danger"), cfg.icon("cross"))],
    )


# ════════════════════════════ активация ═════════════════════════════════════
def activation_menu(cfg: Config, order_id: int) -> InlineKeyboardMarkup:
    """Меню, которое открывается клиенту после подтверждения оплаты."""
    return kb(
        [btn(f"{cfg.emoji('scooter')} Отправить номер самоката", f"act:{order_id}:num",
             cfg.style("primary"), cfg.icon("scooter"))],
        [btn(f"{cfg.emoji('photo')} Отправить скриншот", f"act:{order_id}:pic",
             icon=cfg.icon("photo"))],
        [btn(f"{cfg.emoji('support')} Нужна помощь", "m:support")],
        back(cfg),
    )


def order_open(cfg: Config, order_id: int) -> InlineKeyboardMarkup:
    return kb([btn(f"{cfg.emoji('orders')} Открыть заказ", f"ord:{order_id}")])


# ════════════════════════════ сплит ═════════════════════════════════════════
def split_start(cfg: Config) -> InlineKeyboardMarkup:
    return kb([btn(f"{cfg.emoji('link')} Отправить ссылку", "sp:go",
                   cfg.style("primary"), cfg.icon("link"))], back(cfg))


def split_confirm(cfg: Config) -> InlineKeyboardMarkup:
    return kb(
        [btn(f"{cfg.emoji('check')} Всё верно, продолжить", "sp:conf",
             cfg.style("success"), cfg.icon("check"))],
        [btn(f"{cfg.emoji('link')} Другая ссылка", "sp:go")],
        back(cfg),
    )


def split_delivery(cfg: Config) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    for method in cfg.get("split.delivery", []) or []:
        rows.append([btn(f"{method.get('emoji', '')} {method.get('title')}",
                         f"sp:dlv:{method.get('id')}")])
    rows.append(back(cfg))
    return kb(*rows)


# ════════════════════════════ заказы клиента ════════════════════════════════
def orders_list(cfg: Config, orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [[btn(f"#{o.id} · {cfg.status_label(o.status)} · {cfg.money(o.amount)}", f"ord:{o.id}")]
            for o in orders]
    rows.append(back(cfg))
    return kb(*rows)


def order_card(cfg: Config, order: Order, window_hours: int) -> InlineKeyboardMarkup:
    from .storage.models import ACTIVATION, AWAITING_CHECK, NEW, PAID, PAID_STATUSES, age_hours
    rows: list[Row] = []
    if order.status == NEW:
        rows.append([btn(f"{cfg.emoji('pay')} Оплатить", f"buy:retry:{order.id}",
                         cfg.style("success"), cfg.icon("pay"))])
        rows.append([btn(f"{cfg.emoji('cross')} Отменить", f"cancel:{order.id}",
                         cfg.style("danger"), cfg.icon("cross"))])
    if order.status in (PAID, ACTIVATION):
        rows.append([btn(f"{cfg.emoji('scooter')} Активация", f"act:{order.id}:menu",
                         cfg.style("primary"), cfg.icon("scooter"))])
    if (cfg.get("refund.enabled", True) and order.status in PAID_STATUSES
            and age_hours(order.created_at) <= window_hours):
        rows.append([btn(f"{cfg.emoji('refund')} Оформить возврат", f"rf:{order.id}")])
    if order.status == AWAITING_CHECK:
        rows.append([btn(f"{cfg.emoji('clock')} Ждём проверку оператора", "nop")])
    rows.append([btn(f"{cfg.emoji('support')} Поддержка", "m:support")])
    rows.append(back(cfg, "m:orders", "К заказам"))
    return kb(*rows)


def refund_list(cfg: Config, orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [[btn(f"#{o.id} · {o.title} · {cfg.money(o.amount)}", f"rf:{o.id}")] for o in orders]
    rows.append(back(cfg))
    return kb(*rows)


def support_menu(cfg: Config) -> InlineKeyboardMarkup:
    rows: list[Row] = [[btn(f"{cfg.emoji('support')} Написать оператору", "sup:new",
                            cfg.style("primary"), cfg.icon("support"))]]
    username = str(cfg.get("brand.support_username", "") or "").lstrip("@")
    if username and not username.startswith("your"):
        rows.append([url_btn("💬 Написать напрямую", f"https://t.me/{username}")])
    rows.append(back(cfg))
    return kb(*rows)


def cancel_only(cfg: Config) -> InlineKeyboardMarkup:
    return kb([btn(f"{cfg.emoji('cross')} Отмена", "m:main",
                   cfg.style("danger"), cfg.icon("cross"))])


# ════════════════════════════ админка ═══════════════════════════════════════
def admin_menu(cfg: Config) -> InlineKeyboardMarkup:
    return kb(
        [btn("📥 Заявки на проверке", "a:pending"), btn("📋 Все заказы", "a:orders")],
        [btn("🎫 Обращения", "a:tickets"), btn("📦 Склад кодов", "a:stock")],
        [btn("📊 Статистика", "a:stats"), btn("📢 Рассылка", "a:cast")],
        [btn("🩺 Диагностика", "a:health"),
         btn("🚫 Бан / разбан", "a:ban", cfg.style("danger"))],
        back(cfg),
    )


def admin_orders(cfg: Config, orders: list[Order], source: str = "pending") -> InlineKeyboardMarkup:
    rows = [[btn(f"#{o.id} · {cfg.status_label(o.status)} · {cfg.money(o.amount)}", f"a:ord:{o.id}")]
            for o in orders[:20]]
    rows.append([btn(f"{cfg.emoji('back')} В админку", "a:menu")])
    return kb(*rows)


def admin_order_card(cfg: Config, order: Order) -> InlineKeyboardMarkup:
    from .storage.models import AWAITING_CHECK, NEW, REFUND_APPROVED, REFUND_REQUESTED
    rows: list[Row] = []
    if order.status in (AWAITING_CHECK, NEW):
        rows.append([btn("✅ Подтвердить оплату", f"a:ok:{order.id}",
                         cfg.style("success"), cfg.icon("check")),
                     btn("❌ Отклонить", f"a:no:{order.id}",
                         cfg.style("danger"), cfg.icon("cross"))])
    if order.status == REFUND_REQUESTED:
        rows.append([btn("✅ Одобрить возврат", f"a:rfok:{order.id}",
                         cfg.style("success"), cfg.icon("check")),
                     btn("❌ Отказать", f"a:rfno:{order.id}",
                         cfg.style("danger"), cfg.icon("cross"))])
    if order.status == REFUND_APPROVED:
        rows.append([btn("💸 Возврат отправлен", f"a:rfdone:{order.id}",
                         cfg.style("success"), cfg.icon("refund"))])
    rows.append([btn("✉️ Написать клиенту", f"a:msg:{order.id}"),
                 btn("🔄 Обновить", f"a:ord:{order.id}")])
    rows.append([btn(f"{cfg.emoji('back')} В админку", "a:menu")])
    return kb(*rows)


def admin_stock(cfg: Config, counts: dict[str, int], skus: list[str]) -> InlineKeyboardMarkup:
    rows = [[btn(f"📦 {sku} — {counts.get(sku, 0)} шт · добавить", f"a:stk:{sku}")] for sku in skus]
    rows.append([btn(f"{cfg.emoji('back')} В админку", "a:menu")])
    return kb(*rows)


def admin_tickets(cfg: Config, tickets) -> InlineKeyboardMarkup:
    rows = [[btn(f"#{t.id} · {t.kind} · {t.user_name or t.user_id}"[:60], f"a:tk:{t.id}")]
            for t in tickets[:20]]
    rows.append([btn(f"{cfg.emoji('back')} В админку", "a:menu")])
    return kb(*rows)


def admin_ticket_card(cfg: Config, ticket) -> InlineKeyboardMarkup:
    rows: list[Row] = [[btn("✍️ Ответить", f"a:tkr:{ticket.id}")]]
    if ticket.status == "open":
        rows.append([btn("✅ Закрыть обращение", f"a:tkc:{ticket.id}")])
    if ticket.order_id:
        rows.append([btn(f"🧾 Заказ #{ticket.order_id}", f"a:ord:{ticket.order_id}")])
    rows.append([btn(f"{cfg.emoji('back')} К обращениям", "a:tickets")])
    return kb(*rows)


def confirm_broadcast(cfg: Config) -> InlineKeyboardMarkup:
    return kb([btn("📢 Отправить всем", "a:cast:go", cfg.style("success")),
               btn("❌ Отмена", "a:menu", cfg.style("danger"))])


def refund_wallet_prompt(cfg: Config, order_id: int) -> InlineKeyboardMarkup:
    return kb([btn(f"{cfg.emoji('wallet')} Указать кошелёк", f"rfw:{order_id}")])


def ticket_reply_prompt(cfg: Config, ticket_id: int) -> InlineKeyboardMarkup:
    return kb([btn(f"{cfg.emoji('support')} Ответить оператору", f"sup:re:{ticket_id}")])
