"""Бэкенды хранилища: GitHub Issues (прод) и локальные JSON-файлы (отладка).

GitHub Issues выбраны как БД потому, что бот живёт в GitHub Actions:
раннер эфемерный, а issues переживают любой рестарт, бесплатны и дают
готовый интерфейс для модерации заказов руками.

ВАЖНО: репозиторий обязан быть приватным — в issues лежат заказы и промокоды.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

JSON_BLOCK = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)
DB_LABEL = "bot-db"
API_ROOT = "https://api.github.com"


def _wrap(summary: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    return f"{summary}\n\n<!-- managed by bot, не редактируй JSON руками -->\n```json\n{payload}\n```"


def _unwrap(body: str | None) -> dict:
    if not body:
        return {}
    matches = JSON_BLOCK.findall(body)
    if not matches:
        return {}
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        log.warning("Не разобрал JSON в теле issue")
        return {}


class Backend:
    """Интерфейс хранилища."""

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def get_doc(self, key: str) -> dict: raise NotImplementedError
    async def set_doc(self, key: str, data: dict) -> None: raise NotImplementedError
    async def create_record(self, kind: str, title: str, summary: str, data: dict,
                            labels: list[str] | None = None) -> int: raise NotImplementedError
    async def update_record(self, kind: str, rid: int, title: str, summary: str, data: dict,
                            labels: list[str] | None = None, closed: bool = False) -> None:
        raise NotImplementedError
    async def get_record(self, kind: str, rid: int) -> dict | None: raise NotImplementedError
    async def list_records(self, kind: str, labels: list[str] | None = None,
                           limit: int = 100) -> list[dict]: raise NotImplementedError
    async def comment(self, rid: int, text: str) -> None: ...


class GitHubBackend(Backend):
    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo
        self._session: aiohttp.ClientSession | None = None
        self._docs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    # ── низкий уровень ──────────────────────────────────────────────────────
    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "yandex-go-shop-bot",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
        await self._reload_doc_index()
        log.info("GitHub-хранилище готово: %s (документов: %d)", self.repo, len(self._docs))

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def _api(self, method: str, path: str, *, json_body: dict | None = None,
                   params: dict | None = None, retries: int = 3) -> Any:
        assert self._session is not None, "backend.start() не вызван"
        url = f"{API_ROOT}{path}"
        delay = 1.0
        for attempt in range(1, retries + 1):
            try:
                async with self._session.request(method, url, json=json_body, params=params) as resp:
                    if resp.status in (403, 429) and attempt < retries:
                        wait = float(resp.headers.get("Retry-After", delay))
                        log.warning("GitHub троттлит (%s), жду %.0fс", resp.status, wait)
                        await asyncio.sleep(wait)
                        delay *= 2
                        continue
                    if resp.status >= 400:
                        text = await resp.text()
                        raise RuntimeError(f"GitHub API {resp.status} {method} {path}: {text[:300]}")
                    if resp.status == 204:
                        return None
                    return await resp.json()
            except aiohttp.ClientError as exc:
                if attempt == retries:
                    raise
                log.warning("Сеть до GitHub: %s (попытка %d)", exc, attempt)
                await asyncio.sleep(delay)
                delay *= 2
        return None

    # ── документы (users / stock / settings) ────────────────────────────────
    async def _reload_doc_index(self) -> None:
        issues = await self._api(
            "GET", f"/repos/{self.repo}/issues",
            params={"labels": DB_LABEL, "state": "all", "per_page": "100"},
        ) or []
        self._docs = {}
        for issue in issues:
            title = issue.get("title", "")
            if title.startswith("[db] "):
                self._docs[title[5:].strip()] = issue["number"]

    async def get_doc(self, key: str) -> dict:
        number = self._docs.get(key)
        if number is None:
            return {}
        issue = await self._api("GET", f"/repos/{self.repo}/issues/{number}")
        return _unwrap((issue or {}).get("body"))

    async def set_doc(self, key: str, data: dict) -> None:
        body = _wrap(f"### `{key}`\n\nСлужебный документ бота.", data)
        async with self._lock:
            number = self._docs.get(key)
            if number is None:
                issue = await self._api(
                    "POST", f"/repos/{self.repo}/issues",
                    json_body={"title": f"[db] {key}", "body": body, "labels": [DB_LABEL]},
                )
                self._docs[key] = issue["number"]
                # служебные документы держим закрытыми, чтобы не мешали в списке
                await self._api("PATCH", f"/repos/{self.repo}/issues/{issue['number']}",
                                json_body={"state": "closed"})
            else:
                await self._api("PATCH", f"/repos/{self.repo}/issues/{number}",
                                json_body={"body": body})

    # ── записи (orders / tickets) ───────────────────────────────────────────
    async def create_record(self, kind: str, title: str, summary: str, data: dict,
                            labels: list[str] | None = None) -> int:
        issue = await self._api(
            "POST", f"/repos/{self.repo}/issues",
            json_body={"title": title, "body": _wrap(summary, data),
                       "labels": [kind, *(labels or [])]},
        )
        return int(issue["number"])

    async def update_record(self, kind: str, rid: int, title: str, summary: str, data: dict,
                            labels: list[str] | None = None, closed: bool = False) -> None:
        payload: dict[str, Any] = {"title": title, "body": _wrap(summary, data),
                                   "labels": [kind, *(labels or [])]}
        if closed:
            payload["state"] = "closed"
            payload["state_reason"] = "completed"
        await self._api("PATCH", f"/repos/{self.repo}/issues/{rid}", json_body=payload)

    async def get_record(self, kind: str, rid: int) -> dict | None:
        try:
            issue = await self._api("GET", f"/repos/{self.repo}/issues/{rid}")
        except RuntimeError as exc:
            log.warning("Запись %s#%s не найдена: %s", kind, rid, exc)
            return None
        return _unwrap((issue or {}).get("body")) or None

    async def list_records(self, kind: str, labels: list[str] | None = None,
                           limit: int = 100) -> list[dict]:
        issues = await self._api(
            "GET", f"/repos/{self.repo}/issues",
            params={"labels": ",".join([kind, *(labels or [])]), "state": "all",
                    "per_page": str(min(limit, 100)), "sort": "created", "direction": "desc"},
        ) or []
        return [d for d in (_unwrap(i.get("body")) for i in issues) if d]

    async def comment(self, rid: int, text: str) -> None:
        try:
            await self._api("POST", f"/repos/{self.repo}/issues/{rid}/comments",
                            json_body={"body": text[:60000]})
        except RuntimeError as exc:
            log.warning("Не смог оставить комментарий к #%s: %s", rid, exc)


class LocalBackend(Backend):
    """Файловое хранилище — для локального запуска без GitHub."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.docs_dir = root / "docs"
        self.rec_dir = root / "records"

    async def start(self) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        log.warning("Локальное хранилище: %s (данные не переживут рестарт раннера)", self.root)

    def _doc_path(self, key: str) -> Path:
        return self.docs_dir / f"{key.replace('/', '_').replace(':', '_')}.json"

    async def get_doc(self, key: str) -> dict:
        path = self._doc_path(key)
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    async def set_doc(self, key: str, data: dict) -> None:
        self._doc_path(key).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    def _next_id(self) -> int:
        counter = self.root / "counter"
        value = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(value))
        return value

    async def create_record(self, kind: str, title: str, summary: str, data: dict,
                            labels: list[str] | None = None) -> int:
        rid = self._next_id()
        data = {**data, "id": rid}
        (self.rec_dir / f"{kind}_{rid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        return rid

    async def update_record(self, kind: str, rid: int, title: str, summary: str, data: dict,
                            labels: list[str] | None = None, closed: bool = False) -> None:
        (self.rec_dir / f"{kind}_{rid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    async def get_record(self, kind: str, rid: int) -> dict | None:
        path = self.rec_dir / f"{kind}_{rid}.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    async def list_records(self, kind: str, labels: list[str] | None = None,
                           limit: int = 100) -> list[dict]:
        out = []
        for path in sorted(self.rec_dir.glob(f"{kind}_*.json"), reverse=True)[:limit]:
            out.append(json.loads(path.read_text("utf-8")))
        return out
