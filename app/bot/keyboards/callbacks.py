"""Typed callback data for the admin panel.

Every callback carries only identifiers; the handler re-reads state from the
database, so a stale button can never apply an outdated value.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="nav"):
    section: str


class ChatCB(CallbackData, prefix="chat"):
    #: source | workgroup | dest
    kind: str
    action: str
    id: int = 0
    arg: str = ""


class RouteCB(CallbackData, prefix="route"):
    action: str
    id: int = 0
    arg: int = 0


class OperatorCB(CallbackData, prefix="op"):
    action: str
    id: int = 0
    arg: int = 0


class RuleCB(CallbackData, prefix="rule"):
    #: SUCCESS | FAILED
    status: str
    action: str
    arg: str = ""
    id: int = 0


class AckCB(CallbackData, prefix="ack"):
    status: str
    action: str
    arg: str = ""


class ReportCB(CallbackData, prefix="rep"):
    action: str
    arg: str = ""


class OrderCB(CallbackData, prefix="ord"):
    action: str
    id: int = 0
    arg: str = ""
    #: Two-character switch for manual override: dispatch flag, acknowledge
    #: flag (e.g. "10"). A separate field because aiogram forbids the ":"
    #: separator inside a packed value.
    flags: str = ""


class SettingCB(CallbackData, prefix="set"):
    action: str
    arg: str = ""


class AdminCB(CallbackData, prefix="adm"):
    action: str
    id: int = 0
    arg: str = ""


class SourceCB(CallbackData, prefix="src"):
    action: str
    stage: str = ""
    id: int = 0


class ResultCB(CallbackData, prefix="res"):
    action: str
    status: str = ""
    arg: str = ""


class AuditCB(CallbackData, prefix="aud"):
    action: str
    offset: int = 0
