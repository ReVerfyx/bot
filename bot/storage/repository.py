"""Фасад над бэкендом: пользователи, заказы, склад промокодов, тикеты."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from .backends import Backend, GitHubBackend, LocalBackend
from .models import (
    AWAITING_CHECK, FINAL_STATUSES, OPEN_STATUSES, PAID_STATUSES,
    Order, Ticket, User, utcnow,
)

log = logging.getLogger(__name__)

USER_SHARDS = 16
STOCK_DOC = "stock"


class Repository:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self._users: dict[int, dict] = {}          # shard -> {user_id: dict}
        self._orders: dict[int, Order] = {}
        self._tickets: dict[int, Ticket] = {}
        self._stock: dict[str, list[str]] | None = None

    @classmethod
    def create(cls, github_token: str, github_repo: str, data_dir: Path) -> "Repository":
        if github_token and github_repo and "/" in github_repo:
            return cls(GitHubBackend(github_token, github_repo))
        log.warning("GH_STORE_TOKEN/GH_STORE_REPO не заданы — включаю локальное хранилище")
        return cls(LocalBackend(data_dir))

    async def start(self) -> None:
        await self.backend.start()

    async def close(self) -> None:
        await self.backend.close()

    # ════════════════════════ пользователи ═════════════════════════════════
    async def _shard(self, user_id: int) -> dict:
        idx = user_id % USER_SHARDS
        if idx not in self._users:
            self._users[idx] = await self.backend.get_doc(f"users-{idx}") or {}
        return self._users[idx]

    async def _flush_shard(self, user_id: int) -> None:
        idx = user_id % USER_SHARDS
        await self.backend.set_doc(f"users-{idx}", self._users.get(idx, {}))

    async def touch_user(self, user_id: int, name: str = "", username: str = "") -> User:
        shard = await self._shard(user_id)
        raw = shard.get(str(user_id))
        if raw is None:
            user = User(id=user_id, name=name, username=username)
            shard[str(user_id)] = user.to_dict()
            await self._flush_shard(user_id)
            log.info("Новый пользователь %s (%s)", user_id, username or name)
            return user
        user = User.from_dict(raw)
        changed = user.name != name or user.username != username
        user.name, user.username, user.last_seen = name or user.name, username, utcnow()
        if changed:
            shard[str(user_id)] = user.to_dict()
            await self._flush_shard(user_id)
        else:
            shard[str(user_id)] = user.to_dict()
        return user

    async def get_user(self, user_id: int) -> User | None:
        raw = (await self._shard(user_id)).get(str(user_id))
        return User.from_dict(raw) if raw else None

    async def save_user(self, user: User) -> None:
        shard = await self._shard(user.id)
        shard[str(user.id)] = user.to_dict()
        await self._flush_shard(user.id)

    async def all_users(self) -> list[User]:
        out: list[User] = []
        for idx in range(USER_SHARDS):
            if idx not in self._users:
                self._users[idx] = await self.backend.get_doc(f"users-{idx}") or {}
            out.extend(User.from_dict(raw) for raw in self._users[idx].values())
        return out

    async def set_ban(self, user_id: int, banned: bool) -> User:
        user = await self.get_user(user_id) or User(id=user_id)
        user.banned = banned
        await self.save_user(user)
        return user

    # ════════════════════════ заказы ═══════════════════════════════════════
    @staticmethod
    def _order_labels(order: Order) -> list[str]:
        return [f"status:{order.status}", f"kind:{order.kind}"]

    def _order_summary(self, order: Order) -> str:
        rows = [
            ("Клиент", f"[{order.user_name}](tg://user?id={order.user_id}) `{order.user_id}`"),
            ("Товар", order.title),
            ("Сумма", f"{order.amount} {order.currency}"),
            ("Статус", order.status),
            ("Оплата", f"{order.method} {order.wallet_title}".strip()),
        ]
        if order.item_url:
            rows.append(("Ссылка", order.item_url))
        if order.item_price:
            rows.append(("Цена товара", f"{order.item_price} {order.currency}"))
        if order.address:
            rows.append(("Доставка", f"{order.delivery_title}: {order.address}"))
        if order.proof:
            rows.append(("Подтверждение", f"`{order.proof}`"))
        if order.code:
            rows.append(("Выдан код", f"`{order.code}`"))
        if order.activation_text:
            rows.append(("Данные клиента", order.activation_text))
        if order.reason:
            rows.append(("Причина", order.reason))
        table = "\n".join(f"| {k} | {v} |" for k, v in rows)
        history = "\n".join(f"- {line}" for line in order.history[-20:])
        return (
            f"## Заказ #{order.id or '—'}\n\n"
            f"| Поле | Значение |\n|---|---|\n{table}\n\n"
            f"### История\n{history or '- —'}"
        )

    def _order_title(self, order: Order) -> str:
        who = order.username and f"@{order.username}" or order.user_name or order.user_id
        return f"[order] #{order.id} · {order.title} · {who}"[:250]

    async def create_order(self, order: Order) -> Order:
        order.log("заказ создан")
        rid = await self.backend.create_record(
            "order", f"[order] новый · {order.title}", self._order_summary(order),
            order.to_dict(), self._order_labels(order),
        )
        order.id = rid
        await self.backend.update_record(
            "order", rid, self._order_title(order), self._order_summary(order),
            order.to_dict(), self._order_labels(order),
        )
        self._orders[rid] = order
        user = await self.get_user(order.user_id)
        if user:
            user.orders.append(rid)
            user.orders = user.orders[-100:]
            await self.save_user(user)
        return order

    async def save_order(self, order: Order, *, event: str = "") -> None:
        if event:
            order.log(event)
        order.updated_at = utcnow()
        self._orders[order.id] = order
        await self.backend.update_record(
            "order", order.id, self._order_title(order), self._order_summary(order),
            order.to_dict(), self._order_labels(order),
            closed=order.status in FINAL_STATUSES,
        )
        if event:
            await self.backend.comment(order.id, f"**{event}** · `{utcnow()}`")

    async def get_order(self, order_id: int) -> Order | None:
        if order_id in self._orders:
            return self._orders[order_id]
        raw = await self.backend.get_record("order", order_id)
        if not raw:
            return None
        order = Order.from_dict(raw)
        self._orders[order_id] = order
        return order

    async def list_orders(self, statuses: Iterable[str] | None = None, limit: int = 100) -> list[Order]:
        raw = await self.backend.list_records("order", limit=limit)
        orders = [Order.from_dict(item) for item in raw if item.get("id")]
        for idx, order in enumerate(orders):      # свежие данные из кеша важнее
            cached = self._orders.get(order.id)
            if cached and cached.updated_at >= order.updated_at:
                orders[idx] = cached
        if statuses is not None:
            allowed = set(statuses)
            orders = [o for o in orders if o.status in allowed]
        return sorted(orders, key=lambda o: o.id, reverse=True)

    async def pending_orders(self, limit: int = 50) -> list[Order]:
        return await self.list_orders({AWAITING_CHECK}, limit=limit)

    async def user_orders(self, user_id: int, limit: int = 10) -> list[Order]:
        user = await self.get_user(user_id)
        if not user or not user.orders:
            return []
        orders = []
        for oid in sorted(user.orders, reverse=True)[:limit]:
            order = await self.get_order(oid)
            if order:
                orders.append(order)
        return orders

    # ── подписка на самокаты ────────────────────────────────────────────────
    @staticmethod
    def subscription_days_left(order: Order, cfg) -> int:
        """Сколько дней подписки осталось. 0 — истекла."""
        from .models import age_hours
        _, item = cfg.find_product(order.product_id)
        days = int((item or {}).get("duration_days", 0))
        if not days:
            return 0
        left = days - age_hours(order.paid_at or order.created_at) / 24
        return max(0, int(left + 0.999))           # неполный день считаем за день

    async def active_subscription(self, user_id: int, cfg) -> Order | None:
        """Самый свежий оплаченный тариф, срок которого ещё не вышел."""
        for order in await self.user_orders(user_id, limit=20):
            if order.kind != "scooter" or order.status not in PAID_STATUSES:
                continue
            if self.subscription_days_left(order, cfg) > 0:
                return order
        return None

    async def refundable_orders(self, user_id: int, window_hours: int) -> list[Order]:
        from .models import age_hours
        return [
            o for o in await self.user_orders(user_id, limit=20)
            if o.status in PAID_STATUSES and age_hours(o.created_at) <= window_hours
        ]

    # ════════════════════════ склад промокодов ═════════════════════════════
    async def _load_stock(self) -> dict[str, list[str]]:
        if self._stock is None:
            self._stock = await self.backend.get_doc(STOCK_DOC) or {}
        return self._stock

    async def stock_count(self, sku: str) -> int:
        if not sku:
            return 0
        return len((await self._load_stock()).get(sku, []))

    async def stock_all(self) -> dict[str, int]:
        return {sku: len(codes) for sku, codes in (await self._load_stock()).items()}

    async def stock_add(self, sku: str, codes: list[str]) -> int:
        stock = await self._load_stock()
        seen = set(stock.get(sku, []))
        fresh: list[str] = []
        for code in codes:                        # режем дубликаты и внутри пачки
            if code and code not in seen:
                seen.add(code)
                fresh.append(code)
        stock[sku] = stock.get(sku, []) + fresh
        await self.backend.set_doc(STOCK_DOC, stock)
        return len(fresh)

    async def stock_take(self, sku: str) -> str | None:
        """Достать один свободный код. Пусто — вернёт None (тогда выдаёт оператор)."""
        if not sku:
            return None
        stock = await self._load_stock()
        codes = stock.get(sku) or []
        if not codes:
            return None
        code = codes.pop(0)
        stock[sku] = codes
        await self.backend.set_doc(STOCK_DOC, stock)
        return code

    async def stock_return(self, sku: str, code: str) -> None:
        if not (sku and code):
            return
        stock = await self._load_stock()
        stock[sku] = [code] + (stock.get(sku) or [])
        await self.backend.set_doc(STOCK_DOC, stock)

    # ════════════════════════ тикеты ═══════════════════════════════════════
    def _ticket_summary(self, ticket: Ticket) -> str:
        head = (
            f"## Обращение #{ticket.id or '—'} ({ticket.kind})\n\n"
            f"| Поле | Значение |\n|---|---|\n"
            f"| Клиент | [{ticket.user_name}](tg://user?id={ticket.user_id}) `{ticket.user_id}` |\n"
            f"| Заказ | {ticket.order_id or '—'} |\n"
            f"| Статус | {ticket.status} |\n"
        )
        body = "\n".join(f"- {m}" for m in ticket.messages[-30:])
        return f"{head}\n### Переписка\n{body or '- —'}"

    async def create_ticket(self, ticket: Ticket) -> Ticket:
        ticket.messages.append(f"{utcnow()} · клиент: {ticket.text}")
        rid = await self.backend.create_record(
            "ticket", f"[ticket] {ticket.kind}", self._ticket_summary(ticket),
            ticket.to_dict(), [f"ticket:{ticket.kind}", "status:open"],
        )
        ticket.id = rid
        await self.save_ticket(ticket)
        return ticket

    async def save_ticket(self, ticket: Ticket) -> None:
        ticket.updated_at = utcnow()
        self._tickets[ticket.id] = ticket
        who = ticket.username and f"@{ticket.username}" or ticket.user_name
        await self.backend.update_record(
            "ticket", ticket.id, f"[ticket] #{ticket.id} · {ticket.kind} · {who}"[:250],
            self._ticket_summary(ticket), ticket.to_dict(),
            [f"ticket:{ticket.kind}", f"status:{ticket.status}"],
            closed=ticket.status == "closed",
        )

    async def get_ticket(self, ticket_id: int) -> Ticket | None:
        if ticket_id in self._tickets:
            return self._tickets[ticket_id]
        raw = await self.backend.get_record("ticket", ticket_id)
        if not raw:
            return None
        ticket = Ticket.from_dict(raw)
        self._tickets[ticket_id] = ticket
        return ticket

    async def open_tickets(self, limit: int = 50) -> list[Ticket]:
        raw = await self.backend.list_records("ticket", ["status:open"], limit=limit)
        tickets = [Ticket.from_dict(item) for item in raw if item.get("id")]
        for idx, ticket in enumerate(tickets):    # кеш свежее, чем ответ бэкенда
            cached = self._tickets.get(ticket.id)
            if cached and cached.updated_at >= ticket.updated_at:
                tickets[idx] = cached
        return [t for t in tickets if t.status == "open"]

    # ════════════════════════ статистика ═══════════════════════════════════
    async def stats(self) -> dict:
        orders = await self.list_orders(limit=100)
        done = [o for o in orders if o.status == "done"]
        return {
            "users": len(await self.all_users()),
            "orders": len(orders),
            "done": len(done),
            "pending": len([o for o in orders if o.status == AWAITING_CHECK]),
            "revenue": sum(o.amount for o in done),
            "open": len([o for o in orders if o.status in OPEN_STATUSES]),
            "stock": await self.stock_all(),
        }
