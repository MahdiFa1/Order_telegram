"""Text pattern matching used by the rule engine."""

from __future__ import annotations

import re
from functools import lru_cache

from app.database.models import RuleTextPattern
from app.utils.enums import MatchMode
from app.utils.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=512)
def _compiled(pattern: str, flags: int) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern, flags)
    except re.error as error:
        logger.warning("invalid_regex_pattern", pattern=pattern, error=str(error))
        return None


def matches(text: str, pattern: RuleTextPattern) -> bool:
    """Evaluate one configured pattern against operator text."""
    if not pattern.enabled or not text:
        return False

    candidate = text if pattern.case_sensitive else text.casefold()
    needle = pattern.pattern if pattern.case_sensitive else pattern.pattern.casefold()
    mode = MatchMode(pattern.match_mode)

    if mode is MatchMode.EXACT:
        return candidate.strip() == needle.strip()
    if mode is MatchMode.CONTAINS:
        return needle in candidate
    if mode is MatchMode.REGEX:
        flags = 0 if pattern.case_sensitive else re.IGNORECASE
        compiled = _compiled(pattern.pattern, flags)
        # An invalid regex must never match, and must never crash the bot.
        return bool(compiled.search(text)) if compiled else False
    return False


def first_match(text: str, patterns: list[RuleTextPattern]) -> RuleTextPattern | None:
    for pattern in patterns:
        if matches(text, pattern):
            return pattern
    return None


def validate_pattern(pattern: str, mode: MatchMode) -> str | None:
    """Return an error message when the pattern cannot be used."""
    if not pattern.strip():
        return "Pattern must not be empty."
    if mode is MatchMode.REGEX:
        try:
            re.compile(pattern)
        except re.error as error:
            return f"Invalid regular expression: {error}"
    return None
