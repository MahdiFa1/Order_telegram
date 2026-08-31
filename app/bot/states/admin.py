"""FSM states for the admin panel's text prompts."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddChat(StatesGroup):
    waiting_for_chat_id = State()


class EditChat(StatesGroup):
    waiting_for_title = State()


class AddOperator(StatesGroup):
    waiting_for_user_id = State()


class AddTextPattern(StatesGroup):
    waiting_for_pattern = State()
    waiting_for_mode = State()


class AddRuleReaction(StatesGroup):
    waiting_for_emoji = State()


class SetAckReaction(StatesGroup):
    waiting_for_emoji = State()


class TestAckReaction(StatesGroup):
    waiting_for_target = State()


class FindOrder(StatesGroup):
    waiting_for_query = State()


class CustomRange(StatesGroup):
    waiting_for_range = State()


class EditSetting(StatesGroup):
    waiting_for_value = State()


class AddAdmin(StatesGroup):
    waiting_for_user_id = State()
