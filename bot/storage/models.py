"""Модели данных. Хранятся как JSON внутри GitHub Issues."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

# ── статусы заказа ──────────────────────────────────────────────────────────
NEW = "new"
AWAITING_CHECK = "awaiting_check"
PAID = "paid"
ACTIVATION = "activation"
DONE = "done"
REJECTED = "rejected"
CANCELLED = "cancelled"
EXPIRED = "expired"
REFUND_REQUESTED = "refund_requested"
REFUND_APPROVED = "refund_approved"
REFUNDED = "refunded"

OPEN_STATUSES = {NEW, AWAITING_CHECK, PAID, ACTIVATION}
PAID_STATUSES = {PAID, ACTIVATION, DONE}
FINAL_STATUSES = {DONE, REJECTED, CANCELLED, EXPIRED, REFUNDED}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def age_hours(ts: str) -> float:
    return (datetime.now(timezone.utc) - _parse(ts)).total_seconds() / 3600


def age_minutes(ts: str) -> float:
    return (datetime.now(timezone.utc) - _parse(ts)).total_seconds() / 60


class _Base:
    """Мелкий помощник: dataclass <-> dict, игнорируя лишние ключи."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})  # type: ignore[call-arg]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


@dataclass
class User(_Base):
    id: int
    name: str = ""
    username: str = ""
    joined_at: str = field(default_factory=utcnow)
    last_seen: str = field(default_factory=utcnow)
    banned: bool = False
    orders: list[int] = field(default_factory=list)

    @property
    def mention(self) -> str:
        return f"@{self.username}" if self.username else (self.name or str(self.id))


@dataclass
class Order(_Base):
    id: int = 0
    user_id: int = 0
    user_name: str = ""
    username: str = ""
    kind: str = "scooter"            # scooter | goods | split
    product_id: str = ""
    title: str = ""
    amount: int = 0
    currency: str = "₽"
    status: str = NEW
    method: str = ""                 # cryptobot | manual
    wallet_title: str = ""
    wallet_address: str = ""
    invoice_id: str = ""
    invoice_url: str = ""
    proof: str = ""                  # хеш транзакции
    proof_file_id: str = ""          # file_id скриншота
    item_url: str = ""
    item_title: str = ""
    item_price: int = 0
    fee: int = 0
    delivery_id: str = ""
    delivery_title: str = ""
    address: str = ""
    code: str = ""                   # выданный промокод
    activation_text: str = ""        # номер самоката от клиента
    activation_file_id: str = ""     # скриншот от клиента
    reason: str = ""                 # причина отказа
    refund_wallet: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    paid_at: str = ""                # момент подтверждения оплаты: от него срок подписки
    history: list[str] = field(default_factory=list)

    def log(self, event: str) -> None:
        self.history.append(f"{utcnow()} · {event}")
        self.updated_at = utcnow()

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_paid(self) -> bool:
        return self.status in PAID_STATUSES


@dataclass
class Ticket(_Base):
    id: int = 0
    user_id: int = 0
    user_name: str = ""
    username: str = ""
    kind: str = "support"            # support | refund
    order_id: int = 0
    text: str = ""
    status: str = "open"             # open | closed
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    messages: list[str] = field(default_factory=list)
