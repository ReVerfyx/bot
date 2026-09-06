"""Курс ₽ → крипта для ручной оплаты. Кешируется на 10 минут."""

from __future__ import annotations

import logging
import time

from .cryptobot import CryptoPay

log = logging.getLogger(__name__)
TTL = 600


class Rates:
    def __init__(self, crypto: CryptoPay, fallback: float = 95.0) -> None:
        self.crypto = crypto
        self.fallback = fallback
        self._cache: dict[str, tuple[float, float]] = {}

    async def rub_per(self, asset: str) -> float:
        asset = asset.upper()
        cached = self._cache.get(asset)
        if cached and time.time() - cached[1] < TTL:
            return cached[0]
        rate = await self.crypto.rate(asset, "RUB") if self.crypto.enabled else None
        if not rate:
            rate = self.fallback if asset in {"USDT", "USDC"} else 0.0
        if rate:
            self._cache[asset] = (rate, time.time())
        return rate

    async def convert(self, rub: float, asset: str) -> float:
        rate = await self.rub_per(asset)
        return rub / rate if rate else 0.0
