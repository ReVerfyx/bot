"""Курс ₽ → крипта для ручной оплаты. Кешируется на 10 минут."""

from __future__ import annotations

import logging
import time

from .cryptobot import CryptoPay

log = logging.getLogger(__name__)
TTL = 600


class Rates:
    """Курс берётся у CryptoBot, а без него — из запасных значений конфига.

    Запасной курс важен именно тогда, когда CryptoBot не подключён: клиент
    всё равно должен видеть, сколько монет отправлять на кошелёк.
    """

    def __init__(self, crypto: CryptoPay, fallback: dict[str, float] | float | None = None) -> None:
        self.crypto = crypto
        if isinstance(fallback, (int, float)):     # старый формат: одно число для USDT
            fallback = {"USDT": float(fallback)}
        self.fallback = {str(k).upper(): float(v) for k, v in (fallback or {}).items()}
        self._cache: dict[str, tuple[float, float]] = {}

    async def rub_per(self, asset: str) -> float:
        asset = asset.upper()
        cached = self._cache.get(asset)
        if cached and time.time() - cached[1] < TTL:
            return cached[0]
        rate = await self.crypto.rate(asset, "RUB") if self.crypto.enabled else None
        if not rate:
            rate = self.fallback.get(asset, 0.0)
            if not rate:
                log.warning("Нет курса для %s — клиенту покажем сумму только в рублях. "
                            "Добавь монету в payment.manual_rates_fallback", asset)
        if rate:
            self._cache[asset] = (rate, time.time())
        return rate

    async def convert(self, rub: float, asset: str) -> float:
        rate = await self.rub_per(asset)
        return rub / rate if rate else 0.0
