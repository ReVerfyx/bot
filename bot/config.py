"""Загрузка конфигурации: config.yml (тексты, товары, цены) + .env (секреты)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


def _deep_get(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    out = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.append(int(chunk))
    return out


def fmt_money(amount: float | int, currency: str = "₽") -> str:
    """2000 -> '2 000 ₽' (с неразрывным пробелом, чтобы не рвалось в Telegram)."""
    value = int(round(float(amount)))
    return f"{value:,}".replace(",", " ") + f" {currency}"


def fmt_crypto(amount: float, asset: str) -> str:
    quant = 2 if asset.upper() in {"USDT", "USDC", "BUSD", "TON"} else 8
    text = f"{amount:.{quant}f}".rstrip("0").rstrip(".")
    return f"{text or '0'} {asset.upper()}"


@dataclass(slots=True)
class Settings:
    """Секреты и runtime-параметры из окружения."""

    bot_token: str
    admin_ids: list[int] = field(default_factory=list)
    admin_chat_id: int = 0
    github_token: str = ""
    github_repo: str = ""
    cryptobot_token: str = ""
    cryptobot_api: str = "https://pay.crypt.bot/api"
    admin_secret: str = ""
    run_duration: int = 20700
    data_dir: Path = ROOT / ".data"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан — добавь его в секреты репозитория или .env")
        return cls(
            bot_token=token,
            admin_ids=_int_list(os.getenv("ADMIN_IDS")),
            admin_chat_id=(_int_list(os.getenv("ADMIN_CHAT_ID")) or [0])[0],
            github_token=(os.getenv("GH_STORE_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip(),
            github_repo=(os.getenv("GH_STORE_REPO") or os.getenv("GITHUB_REPOSITORY") or "").strip(),
            cryptobot_token=os.getenv("CRYPTOBOT_TOKEN", "").strip(),
            cryptobot_api=os.getenv("CRYPTOBOT_API", "https://pay.crypt.bot/api").rstrip("/"),
            admin_secret=os.getenv("ADMIN_SECRET", "").strip(),
            run_duration=int(os.getenv("RUN_DURATION_SECONDS", "20700")),
            data_dir=Path(os.getenv("DATA_DIR", str(ROOT / ".data"))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


class Config:
    """Обёртка над config.yml с рендером текстов и доступом к каталогу."""

    def __init__(self, data: dict, settings: Settings) -> None:
        self.data = data
        self.settings = settings
        self._admins = set(settings.admin_ids) | set(self.get("access.admins", []) or [])
        self.admin_chat_id = settings.admin_chat_id or int(self.get("access.admin_chat_id", 0) or 0)

    # ── загрузка ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, settings: Settings, path: str | Path | None = None) -> "Config":
        cfg_path = Path(path or os.getenv("CONFIG_PATH") or ROOT / "config.yml")
        if not cfg_path.is_absolute():
            cfg_path = ROOT / cfg_path
        with cfg_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data, settings)

    # ── доступ ──────────────────────────────────────────────────────────────
    def get(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.data, path, default)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self._admins

    @property
    def stealth(self) -> bool:
        """Скрытый админ: панель не видна клиентам и не упоминается в интерфейсе."""
        return bool(self.get("access.stealth", True))

    @property
    def support_contact(self) -> str:
        """Публичный контакт поддержки или нейтральная замена, если он не указан."""
        username = str(self.get("brand.support_username", "") or "").strip()
        if username and not username.startswith("@your"):
            return username
        return str(self.get("brand.support_fallback", "через поддержку в боте"))

    @property
    def admins(self) -> list[int]:
        return sorted(self._admins)

    @property
    def currency(self) -> str:
        return str(self.get("brand.currency", "₽"))

    def money(self, amount: float | int) -> str:
        return fmt_money(amount, self.currency)

    def status_label(self, status: str) -> str:
        return str(self.get(f"statuses.{status}", status))

    # ── декор ───────────────────────────────────────────────────────────────
    def emoji(self, key: str) -> str:
        """Обычное эмодзи — годится и для кнопок, и для текста."""
        return str(self.get(f"decor.emoji.{key}", "") or "")

    def deco(self, key: str) -> str:
        """Эмодзи для текста: премиум custom emoji, если задан id."""
        base = self.emoji(key)
        custom = str(self.get(f"decor.custom_emoji.{key}", "") or "").strip()
        if custom and base:
            return f'<tg-emoji emoji-id="{custom}">{base}</tg-emoji>'
        return base

    def icon(self, key: str) -> str | None:
        """custom_emoji_id для иконки на кнопке (Bot API 9.4) или None."""
        return str(self.get(f"decor.custom_emoji.{key}", "") or "").strip() or None

    def style(self, name: str) -> str | None:
        """Цвет кнопки: danger / success / primary. None — стандартный вид."""
        return name if self.get("decor.button_styles", True) else None

    def sticker(self, key: str) -> str:
        return str(self.get(f"decor.stickers.{key}", "") or "").strip()

    # ── тексты ──────────────────────────────────────────────────────────────
    def _base_vars(self) -> dict[str, str]:
        vars_: dict[str, str] = {
            "brand": str(self.get("brand.name", "")),
            "support": self.support_contact,
            "channel": str(self.get("brand.channel_url", "")),
            "backup": str(self.get("brand.backup_url", "")),
            "reviews": str(self.get("brand.reviews_url", "")),
            "currency": self.currency,
        }
        for key in (self.get("decor.emoji", {}) or {}):
            vars_[f"e_{key}"] = self.deco(key)
        # алиасы под частые опечатки в шаблонах
        vars_.setdefault("e_success", self.deco("check"))
        vars_.setdefault("e_error", self.deco("cross"))
        return vars_

    def render(self, template: str, **kwargs: Any) -> str:
        values = self._base_vars()
        values.update({k: ("" if v is None else str(v)) for k, v in kwargs.items()})
        return Template(str(template)).safe_substitute(values).strip()

    def text(self, key: str, **kwargs: Any) -> str:
        template = self.get(f"texts.{key}")
        if template is None:
            log.warning("В config.yml нет texts.%s", key)
            return f"⚠️ texts.{key} не задан в config.yml"
        return self.render(template, **kwargs)

    # ── каталог ─────────────────────────────────────────────────────────────
    def section_items(self, section: str) -> list[dict]:
        if not self.get(f"{section}.enabled", True):
            return []
        return list(self.get(f"{section}.items", []) or [])

    def find_product(self, product_id: str) -> tuple[str, dict] | tuple[None, None]:
        for section in ("scooters", "goods"):
            for item in self.section_items(section):
                if str(item.get("id")) == str(product_id):
                    return section, item
        return None, None

    # ── сплит ───────────────────────────────────────────────────────────────
    def split_fee(self, item_price: float) -> int | None:
        """Комиссия сервиса по стоимости товара. None — вне лимитов."""
        for tier in self.get("split.tiers", []) or []:
            if item_price <= float(tier.get("up_to", 0)):
                return int(tier.get("fee", 0))
        return None

    def split_tiers_text(self) -> str:
        lines = []
        low = 0
        for tier in self.get("split.tiers", []) or []:
            up_to = int(tier.get("up_to", 0))
            fee = int(tier.get("fee", 0))
            lines.append(f"• {self.money(low)} — {self.money(up_to)}  →  <b>{self.money(fee)}</b>")
            low = up_to + 1
        return "\n".join(lines)
