"""Самодиагностика: что подключено и в каком состоянии магазин.

Нужна, чтобы после запуска воркфлоу не гадать «поднялось или нет»: бот сам
присылает сводку админам при старте смены и по команде /health.
"""

from __future__ import annotations

from aiogram import Bot

from ..config import Config
from ..services.cryptobot import CryptoPay
from ..storage import Repository
from ..storage.backends import GitHubBackend


def _yes(flag: bool) -> str:
    return "✅" if flag else "❌"


# следы шаблона: такой текст клиент получать не должен
STUB_MARKS = ("напиши сюда", "впиши свой", "впиши сюда", "свой текст",
              "текст инструкции", "текст для")


def _is_stub(text: str) -> bool:
    lowered = (text or "").lower()
    return not lowered.strip() or any(mark in lowered for mark in STUB_MARKS)


async def report(cfg: Config, repo: Repository, crypto: CryptoPay,
                 bot: Bot | None = None, run_seconds: int = 0) -> str:
    """Короткая сводка о состоянии. Всё, что может не подняться — с галочкой."""
    lines: list[str] = ["🩺 <b>Диагностика YAservice</b>", ""]

    if bot is not None:
        me = await bot.me()
        lines.append(f"🤖 Бот: @{me.username} (<code>{me.id}</code>)")

    github = isinstance(repo.backend, GitHubBackend)
    storage = f"GitHub Issues · <code>{repo.backend.repo}</code>" if github else \
              "локальные файлы — <b>данные пропадут при рестарте</b>"
    lines += [
        f"{_yes(github)} Хранилище: {storage}",
        f"{_yes(crypto.enabled)} CryptoBot: {'подключён' if crypto.enabled else 'выключен, только ручная оплата'}",
        f"{_yes(bool(cfg.owner_id))} Панель: "
        + (f"только владелец <code>{cfg.owner_id}</code>" if cfg.get("access.owner_only", True)
           else f"все админы ({len(cfg.admins)})")
        + (f" · уведомления ещё у {len(cfg.admins) - 1}" if len(cfg.admins) > 1 else ""),
        f"{_yes(bool(cfg.admin_chat_id))} Чат заявок: "
        f"{'<code>' + str(cfg.admin_chat_id) + '</code>' if cfg.admin_chat_id else 'нет, заявки идут в личку'}",
    ]

    wallets = cfg.get("payment.manual.wallets", []) or []
    # заглушки бывают и заглавными (TXXXX…), и строчными (bc1qxxxx…)
    unset = [w.get("title") for w in wallets if "xxxx" in str(w.get("address", "")).lower()]
    lines.append(f"{_yes(not unset)} Кошельки: {len(wallets)} шт"
                 + (f" — <b>заглушки не заменены: {', '.join(map(str, unset))}</b>" if unset else ""))

    # тексты, которые клиент получает после активации, легко забыть заполнить
    stubs = [str(i.get("id")) for i in cfg.section_items("scooters") + cfg.section_items("goods")
             if _is_stub(str(i.get("instructions", "")))]
    if _is_stub(str(cfg.get("split.instructions", ""))):
        stubs.append("split")
    lines.append(f"{_yes(not stubs)} Тексты активации: "
                 + (f"<b>заглушки у {', '.join(stubs)}</b>" if stubs else "заполнены"))

    scooters = len(cfg.section_items("scooters"))
    goods = len(cfg.section_items("goods"))
    stock = await repo.stock_all()
    stock_text = ", ".join(f"{k}:{v}" for k, v in stock.items()) if stock else "пусто"
    lines += [
        f"{_yes(bool(scooters or goods))} Каталог: {scooters} тарифов, {goods} товаров",
        f"📦 Склад кодов: {stock_text}",
        f"🧩 Сплит: {'вкл' if cfg.get('split.enabled', True) else 'выкл'}, "
        f"лимит {cfg.money(cfg.get('split.max_price', 0))}",
        f"🎨 Цветные кнопки: {'вкл' if cfg.get('decor.button_styles', True) else 'выкл'} · "
        f"премиум-эмодзи: {'вкл' if cfg.custom_emoji_on else 'выкл'}",
        f"🕶 Скрытый админ: {'вкл' if cfg.stealth else 'выкл'}",
    ]

    pending = await repo.pending_orders()
    tickets = await repo.open_tickets()
    lines += ["", f"🔍 На проверке: <b>{len(pending)}</b> · 🎫 обращений: <b>{len(tickets)}</b>"]
    if run_seconds:
        lines.append(f"⏱ Смена: {run_seconds // 3600} ч {run_seconds % 3600 // 60} мин, "
                     f"дальше автоперезапуск")
    return "\n".join(lines)
