"""Разбор ссылки на товар Яндекса: название, цена, картинка.

Маркет активно защищается от парсинга, поэтому логика best-effort:
что удалось вытащить — показываем, цену не нашли — спрашиваем у клиента.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
META_RE = re.compile(r"<meta[^>]+>", re.IGNORECASE)
ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""")
LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                   re.DOTALL | re.IGNORECASE)
PRICE_RE = re.compile(r'"price"\s*:\s*"?(\d[\d\s.,]*)"?')
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


@dataclass
class ItemInfo:
    url: str
    title: str = ""
    price: int = 0
    image: str = ""
    ok: bool = False


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(").,;") if match else None


def domain_allowed(url: str, allowed: list[str]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def _to_int(raw: str | float | int | None) -> int:
    if raw is None:
        return 0
    text = re.sub(r"[^\d.,]", "", str(raw)).replace(" ", "")
    if not text:
        return 0
    text = text.replace(",", ".")
    if text.count(".") > 1:                       # 1.234.567 → 1234567
        text = text.replace(".", "")
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def _metas(page: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in META_RE.findall(page[:400_000]):
        attrs = dict(ATTR_RE.findall(tag))
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        value = attrs.get("content")
        if key and value:
            found.setdefault(key.lower(), html.unescape(value.strip()))
    return found


def _from_ld_json(page: str) -> tuple[str, int, str]:
    for blob in LD_RE.findall(page[:400_000]):
        try:
            data = json.loads(blob.strip())
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if "Product" not in str(node.get("@type", "")):
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else ""
            return (str(node.get("name", "")), _to_int((offers or {}).get("price")), str(image or ""))
    return "", 0, ""


def parse_page(url: str, page: str) -> ItemInfo:
    info = ItemInfo(url=url)
    name, price, image = _from_ld_json(page)
    metas = _metas(page)
    info.title = name or metas.get("og:title", "") or ""
    if not info.title:
        match = TITLE_RE.search(page)
        info.title = html.unescape(match.group(1)).strip() if match else ""
    info.image = image or metas.get("og:image", "")
    info.price = price or _to_int(metas.get("product:price:amount") or metas.get("price"))
    if not info.price:
        match = PRICE_RE.search(page[:400_000])
        info.price = _to_int(match.group(1)) if match else 0
    info.title = re.sub(r"\s+", " ", info.title)[:180].strip(" —-|")
    info.ok = bool(info.title)
    return info


async def fetch_item(url: str, timeout: int = 12) -> ItemInfo:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    try:
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
                final_url = str(resp.url)
                if resp.status >= 400:
                    log.info("Страница товара вернула %s: %s", resp.status, final_url)
                    return ItemInfo(url=final_url)
                page = await resp.text(errors="ignore")
    except Exception as exc:                       # noqa: BLE001 — парсер не должен ронять бота
        log.info("Не смог загрузить %s: %s", url, exc)
        return ItemInfo(url=url)
    return parse_page(final_url, page)
