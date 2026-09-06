"""Точка входа. Бот живёт в GitHub Actions ограниченное время и перезапускается."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramConflictError, TelegramNetworkError, TelegramUnauthorizedError,
)
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config, Settings
from .handlers import build_router
from .middlewares import ThrottleMiddleware, UserMiddleware
from .services.cryptobot import CryptoPay
from .services.emoji_guard import CustomEmojiGuard
from .services.rates import Rates
from .storage import Repository

log = logging.getLogger("bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s │ %(levelname)-7s │ %(name)-22s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def _shutdown(dispatcher: Dispatcher) -> None:
    """stop_polling() — корутина и ругается, если polling ещё не стартовал."""
    with contextlib.suppress(RuntimeError):
        await dispatcher.stop_polling()


async def _stop_after(seconds: int, dispatcher: Dispatcher) -> None:
    """Плановая остановка: workflow перезапустит бота свежим раннером."""
    await asyncio.sleep(seconds)
    log.info("Отработал %d с — глушу polling для планового перезапуска", seconds)
    await _shutdown(dispatcher)


async def run() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    cfg = Config.load(settings)

    if not cfg.admins:
        log.warning("Не задан ни один админ — заявки будет некому подтверждать")

    repo = Repository.create(settings.github_token, settings.github_repo, settings.data_dir)
    await repo.start()

    crypto = CryptoPay(settings.cryptobot_token, settings.cryptobot_api)
    await crypto.start()
    rates = Rates(crypto, float(cfg.get("payment.manual_rate_fallback", 95.0)))

    bot = Bot(settings.bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    # если премиум-эмодзи окажутся недоступны, бот продолжит работать на обычных
    bot.session.middleware(CustomEmojiGuard())
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["cfg"] = cfg
    dispatcher["repo"] = repo
    dispatcher["crypto"] = crypto
    dispatcher["rates"] = rates

    dispatcher.update.outer_middleware(UserMiddleware(cfg, repo))
    dispatcher.message.middleware(ThrottleMiddleware(0.4))
    dispatcher.callback_query.middleware(ThrottleMiddleware(0.35))
    dispatcher.include_router(build_router())

    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        log.info("Получен сигнал остановки")
        loop.create_task(_shutdown(dispatcher))

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

    timer = asyncio.create_task(_stop_after(settings.run_duration, dispatcher))
    try:
        me = await bot.get_me()
        log.info("Запущен @%s (id=%s), сессия на %d с", me.username, me.id, settings.run_duration)
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, handle_signals=False,
                                       allowed_updates=dispatcher.resolve_used_update_types())
    except TelegramUnauthorizedError:
        log.error("BOT_TOKEN недействителен")
        raise
    except TelegramConflictError:
        log.error("Уже запущен другой экземпляр бота — этот раннер выходит")
    except TelegramNetworkError as exc:
        log.error("Сеть до Telegram недоступна: %s — смена завершится, cron поднимет новую", exc)
    finally:
        timer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await timer
        await crypto.close()
        await repo.close()
        await bot.session.close()
        log.info("Остановлен штатно")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Прервано с клавиатуры")


if __name__ == "__main__":
    main()
