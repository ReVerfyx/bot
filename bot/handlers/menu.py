"""Стартовый экран, главное меню, каталоги и карточки товаров."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..common import send_sticker, show
from ..config import Config
from ..storage import Repository, User

router = Router(name="menu")


async def render_main(event: Message | CallbackQuery, cfg: Config, is_admin: bool) -> None:
    await show(event, cfg.text("menu"), ui.main_menu(cfg, is_admin))


@router.message(CommandStart())
async def cmd_start(message: Message, cfg: Config, user: User, is_admin: bool,
                    state: FSMContext) -> None:
    await state.clear()
    await send_sticker(message.bot, cfg, message.chat.id, "welcome")
    await message.answer(
        cfg.text("start", name=user.name.split()[0] if user.name else "друг"),
        reply_markup=ui.main_menu(cfg, is_admin),
        disable_web_page_preview=True,
    )


@router.message(Command("menu", "help"))
async def cmd_menu(message: Message, cfg: Config, is_admin: bool, state: FSMContext) -> None:
    await state.clear()
    await render_main(message, cfg, is_admin)


@router.callback_query(F.data == "m:main")
async def cb_main(call: CallbackQuery, cfg: Config, is_admin: bool, state: FSMContext) -> None:
    await state.clear()
    await render_main(call, cfg, is_admin)
    await call.answer()


@router.callback_query(F.data == "m:about")
async def cb_about(call: CallbackQuery, cfg: Config) -> None:
    await show(call, cfg.text("about"), ui.about_menu(cfg))
    await call.answer()


@router.callback_query(F.data.in_({"m:scooters", "m:goods"}))
async def cb_catalog(call: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    await state.clear()
    section = "scooters" if call.data == "m:scooters" else "goods"
    items = cfg.section_items(section)
    if not items:
        text = cfg.text("goods_empty") if section == "goods" else cfg.text("catalog_empty")
        await show(call, text, ui.main_menu(cfg, cfg.is_admin(call.from_user.id)))
        await call.answer()
        return
    intro = cfg.text("scooters_intro") if section == "scooters" else cfg.text("goods_intro")
    await show(call, intro, ui.catalog(cfg, section))
    await call.answer()


async def stock_label(cfg: Config, repo: Repository, item: dict) -> tuple[str, bool]:
    """Подпись о наличии и признак «можно покупать»."""
    sku = str(item.get("sku") or "")
    if not sku:
        return "есть", True
    count = await repo.stock_count(sku)
    if count == 0:
        # склад пуст — выдаёт оператор вручную, продажу не блокируем
        return "под заказ", True
    return f"{count} шт", True


@router.callback_query(F.data.startswith("p:"))
async def cb_product(call: CallbackQuery, cfg: Config, repo: Repository) -> None:
    product_id = call.data.split(":", 1)[1]
    section, item = cfg.find_product(product_id)
    if not item:
        await call.answer("Позиция не найдена", show_alert=True)
        return
    stock, buyable = await stock_label(cfg, repo, item)
    key = "scooter_card" if section == "scooters" else "goods_card"
    text = cfg.text(
        key,
        title=item.get("title", ""),
        badge=item.get("badge", ""),
        description=str(item.get("description", "")).strip(),
        price=cfg.money(item.get("price", 0)),
        duration=item.get("duration_days", ""),
        stock=stock,
    )
    await show(call, text, ui.product_card(cfg, product_id, buyable))
    await call.answer()


@router.callback_query(F.data == "nop")
async def cb_nop(call: CallbackQuery) -> None:
    await call.answer()
