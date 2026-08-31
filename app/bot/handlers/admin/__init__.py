"""Admin panel routers, assembled in menu order."""

from aiogram import Router

from app.bot.handlers.admin import (
    chats,
    menu,
    operators,
    orders,
    reactions,
    reports,
    routing,
    rules,
    settings,
)

admin_router = Router(name="admin")
admin_router.include_router(menu.router)
admin_router.include_router(chats.router)
admin_router.include_router(routing.router)
admin_router.include_router(operators.router)
admin_router.include_router(rules.router)
admin_router.include_router(reactions.router)
admin_router.include_router(reports.router)
admin_router.include_router(orders.router)
admin_router.include_router(settings.router)

__all__ = ["admin_router"]
