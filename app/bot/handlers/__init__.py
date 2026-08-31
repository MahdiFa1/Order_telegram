"""Root router.

Admin handlers are registered first because they are private-chat only and
tightly filtered; order and operator handlers then see everything else.
"""

from aiogram import Router

from app.bot.handlers import operators, orders
from app.bot.handlers.admin import admin_router

root_router = Router(name="root")
root_router.include_router(admin_router)
root_router.include_router(orders.router)
root_router.include_router(operators.router)

__all__ = ["root_router"]
