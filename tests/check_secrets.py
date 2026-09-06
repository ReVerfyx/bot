"""Защита от утечки секретов в репозиторий.

Сканирует отслеживаемые git-ом файлы на токены и ключи. Секреты должны жить
только в GitHub Actions Secrets и в локальном .env, который никогда
не коммитится.

Запуск: python -m tests.check_secrets
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).name

# Паттерны собраны из частей, чтобы файл не ловил сам себя
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("токен Telegram-бота или Crypto Pay", re.compile(r"\b\d{5,12}:" + "AA" + r"[\w-]{30,}\b")),
    ("GitHub PAT (classic)", re.compile(r"\bghp" + r"_[A-Za-z0-9]{36}\b")),
    ("GitHub PAT (fine-grained)", re.compile(r"\bgithub" + r"_pat_[A-Za-z0-9_]{50,}\b")),
    ("приватный ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip"}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def main() -> int:
    problems: list[str] = []

    for path in tracked_files():
        if path.name == SELF or path.suffix in SKIP_SUFFIXES or not path.is_file():
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            if path.name != ".env.example":
                problems.append(f"{path.name}: файл с секретами не должен лежать в git")
                continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                shown = match.group(0)[:12] + "…"
                rel = path.relative_to(ROOT)
                problems.append(f"{rel}:{line} — похоже на {label}: {shown}")

    if problems:
        print("❌ В репозитории найдены секреты:")
        for line in problems:
            print("  •", line)
        print("\nЧто делать: немедленно отозвать найденный токен, удалить его из файла")
        print("и хранить только в GitHub Actions Secrets или локальном .env (он в .gitignore).")
        return 1

    print(f"✅ Секретов в репозитории нет (проверено файлов: {len(tracked_files())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
