"""Rendering of the daily order number."""

from __future__ import annotations


def render_display_number(number: int, prefix: str, template: str) -> str:
    """Format ``order15`` (default) or any configured variant, e.g. ``ORD-15``.

    The template is stored in the database so the format can be changed from
    the admin panel without a code change.
    """
    try:
        return template.format(prefix=prefix, number=number)
    except (KeyError, IndexError, ValueError):
        return f"{prefix}{number}"
