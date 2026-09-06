"""Проверка целостности config.yml: все ключи текстов на месте, каталог валиден.

Запуск: BOT_TOKEN=1:test python -m tests.check_config
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:test")

from bot.config import Config, Settings  # noqa: E402

TEXT_CALL = re.compile(r"""cfg\.text\(\s*["']([\w:.-]+)["']""")


def used_text_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "bot").rglob("*.py"):
        keys |= set(TEXT_CALL.findall(path.read_text("utf-8")))
    return keys


def main() -> int:
    cfg = Config.load(Settings.from_env())
    problems: list[str] = []

    declared = set((cfg.get("texts", {}) or {}).keys())
    missing = sorted(used_text_keys() - declared)
    if missing:
        problems.append("В config.yml нет текстов: " + ", ".join(missing))

    ids: set[str] = set()
    for section in ("scooters", "goods"):
        for item in cfg.section_items(section):
            pid = str(item.get("id", ""))
            if not pid:
                problems.append(f"{section}: у позиции нет id")
            if pid in ids:
                problems.append(f"{section}: дублируется id «{pid}»")
            ids.add(pid)
            if not item.get("title"):
                problems.append(f"{section}/{pid}: нет title")
            if int(item.get("price", 0)) <= 0:
                problems.append(f"{section}/{pid}: цена должна быть больше нуля")

    tiers = cfg.get("split.tiers", []) or []
    if cfg.get("split.enabled", True):
        if not tiers:
            problems.append("split.tiers пуст — не из чего считать комиссию")
        bounds = [int(t.get("up_to", 0)) for t in tiers]
        if bounds != sorted(bounds):
            problems.append("split.tiers должны идти по возрастанию up_to")
        if bounds and bounds[-1] < int(cfg.get("split.max_price", 0)):
            problems.append("последний up_to меньше split.max_price — часть цен без комиссии")
        if not cfg.get("split.delivery"):
            problems.append("не задан ни один способ доставки в split.delivery")

    if not (cfg.get("payment.manual.wallets") or cfg.get("payment.cryptobot.enabled")):
        problems.append("не настроен ни один способ оплаты")

    for status in ("new", "awaiting_check", "paid", "activation", "done", "rejected",
                   "cancelled", "expired", "refund_requested", "refund_approved", "refunded"):
        if not cfg.get(f"statuses.{status}"):
            problems.append(f"нет подписи для статуса «{status}»")

    # проверяем, что шаблоны рендерятся и в них не осталось незакрытых плейсхолдеров
    for key in declared:
        rendered = cfg.text(key, **{name: "X" for name in
                                    ("order_id", "title", "amount", "price", "status", "reason",
                                     "code", "instructions", "ttl", "duration", "stock", "badge",
                                     "description", "url", "item_price", "fee", "total", "min",
                                     "max", "tiers", "domains", "ask", "name", "policy", "window",
                                     "ticket_id", "text", "user_id", "user_name", "method",
                                     "created", "extra", "wallet_title", "crypto_amount",
                                     "address", "note", "count", "sku", "ok", "fail", "users",
                                     "orders", "done", "pending", "revenue", "state", "tickets")})
        if "$" in rendered:
            leftovers = re.findall(r"\$\w+", rendered)
            problems.append(f"texts.{key}: неизвестные плейсхолдеры {leftovers}")

    if problems:
        print("❌ Проблемы в конфиге:")
        for line in problems:
            print("  •", line)
        return 1
    print(f"✅ config.yml в порядке: {len(declared)} текстов, {len(ids)} позиций каталога")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
