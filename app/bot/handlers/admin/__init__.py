"""Admin panel routers, assembled in menu order."""

from aiogram import Router

from app.bot.handlers.admin import (
    admins,
    chats,
    menu,
    operators,
    orders,
    reactions,
    reports,
    result_content,
    routing,
    rules,
    settings,
    source_reactions,
)

admin_router = Router(name="admin")
admin_router.include_router(menu.router)
admin_router.include_router(chats.router)
admin_router.include_router(routing.router)
admin_router.include_router(operators.router)
admin_router.include_router(rules.router)
admin_router.include_router(reactions.router)
admin_router.include_router(source_reactions.router)
admin_router.include_router(result_content.router)
admin_router.include_router(reports.router)
admin_router.include_router(orders.router)
admin_router.include_router(settings.router)
admin_router.include_router(admins.router)

__all__ = ["admin_router"]
