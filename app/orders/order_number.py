"""Extraction and validation of the store order number.

The number the shop assigned lives on the **last line** of the message posted
in the source channel. It is read once, at intake, and stored on the order --
so nothing an operator later types in the work group (``420x2✅`` and the
like) can affect it.

Persian and Arabic-Indic digits are normalised first, because a number typed
as ``۱۲۳۴۵۶۷`` is the same order as ``1234567``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: ۰-۹ (Persian) and ٠-٩ (Arabic-Indic) map onto ASCII digits.
_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)

#: A maximal run of digits, so "12345678" is never read as a 7-digit number
#: with a stray trailing digit.
_DIGIT_RUN = re.compile(r"\d+")

DEFAULT_LENGTH = 7
MIN_LENGTH = 3
MAX_LENGTH = 20


class RejectReason(StrEnum):
    NO_TEXT = "NO_TEXT"
    MISSING = "MISSING"
    WRONG_LENGTH = "WRONG_LENGTH"


@dataclass(frozen=True, slots=True)
class OrderNumberResult:
    number: str | None
    reason: RejectReason | None = None
    #: Digit runs that were present but the wrong length, for the audit trail.
    found: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.number is not None


def normalise_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def last_line(text: str) -> str:
    """The last line that carries any content."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def extract(text: str | None, length: int = DEFAULT_LENGTH) -> OrderNumberResult:
    """Read the order number from the last line of ``text``.

    A line may carry a label (``شماره سفارش: 1234567``) or the bare number.
    When several runs of the required length appear, the last one wins --
    that is the position the number is written in.
    """
    if not text or not text.strip():
        return OrderNumberResult(None, RejectReason.NO_TEXT)

    line = normalise_digits(last_line(text))
    runs = tuple(_DIGIT_RUN.findall(line))
    if not runs:
        return OrderNumberResult(None, RejectReason.MISSING)

    exact = [run for run in runs if len(run) == length]
    if not exact:
        return OrderNumberResult(None, RejectReason.WRONG_LENGTH, runs)
    return OrderNumberResult(exact[-1], None, runs)


def clamp_length(value: int) -> int:
    return max(MIN_LENGTH, min(MAX_LENGTH, value))


def describe(result: OrderNumberResult, length: int) -> str:
    """Short English explanation for the audit trail."""
    if result.ok:
        return f"order number {result.number}"
    if result.reason is RejectReason.NO_TEXT:
        return "message has no text to read an order number from"
    if result.reason is RejectReason.MISSING:
        return "last line contains no digits"
    found = ", ".join(result.found)
    return f"last line has {found} but {length} digits are required"
