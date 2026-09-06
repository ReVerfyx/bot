"""Клиент Crypto Pay API (@CryptoBot). Счёт в рублях, оплата криптой."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


class CryptoPay:
    def __init__(self, token: str, api: str = "https://pay.crypt.bot/api") -> None:
        self.token = token
        self.api = api.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def start(self) -> None:
        if not self.enabled:
            return
        self._session = aiohttp.ClientSession(
            headers={"Crypto-Pay-API-Token": self.token},
            timeout=aiohttp.ClientTimeout(total=20),
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def _call(self, method: str, **params: Any) -> dict | None:
        if not self._session:
            return None
        try:
            async with self._session.get(f"{self.api}/{method}", params=params) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as exc:
            log.warning("CryptoBot %s: %s", method, exc)
            return None
        if not data.get("ok"):
            log.warning("CryptoBot %s вернул ошибку: %s", method, data.get("error"))
            return None
        return data.get("result")

    async def create_invoice(self, amount: float, *, fiat: str = "RUB", asset: str = "USDT",
                             in_fiat: bool = True, description: str = "", payload: str = "",
                             expires_in: int = 3600) -> dict | None:
        params: dict[str, Any] = {
            "description": description[:1024],
            "payload": payload[:4096],
            "expires_in": expires_in,
            "allow_comments": "false",
            "allow_anonymous": "true",
        }
        if in_fiat:
            params.update({"currency_type": "fiat", "fiat": fiat, "amount": f"{amount:.2f}"})
            # без accepted_assets счёт можно оплатить любой монетой, включённой
            # в приложении CryptoBot; список сужаем, только если он задан явно
            if asset:
                params["accepted_assets"] = asset
        else:
            params.update({"currency_type": "crypto", "asset": asset or "USDT",
                           "amount": f"{amount:.6f}"})
        result = await self._call("createInvoice", **params)
        if not result:
            return None
        return {
            "invoice_id": str(result.get("invoice_id", "")),
            "url": result.get("bot_invoice_url") or result.get("pay_url") or "",
            "status": result.get("status", "active"),
        }

    async def invoice_status(self, invoice_id: str) -> str:
        result = await self._call("getInvoices", invoice_ids=invoice_id)
        items = (result or {}).get("items") or []
        return items[0].get("status", "unknown") if items else "unknown"

    async def rate(self, asset: str, fiat: str = "RUB") -> float | None:
        result = await self._call("getExchangeRates")
        for row in result or []:
            if row.get("source") == asset.upper() and row.get("target") == fiat.upper():
                try:
                    return float(row.get("rate"))
                except (TypeError, ValueError):
                    return None
        return None
