from aiogram import Router

from . import activation, admin, fallback, menu, orders, payment, split, support


def build_router() -> Router:
    """Порядок важен: сначала конкретные хендлеры, fallback — последним."""
    router = Router(name="root")
    router.include_router(menu.router)
    router.include_router(orders.router)
    router.include_router(payment.router)
    router.include_router(activation.router)
    router.include_router(split.router)
    router.include_router(support.router)
    router.include_router(admin.router)
    router.include_router(fallback.router)
    return router
