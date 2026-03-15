"""Shared interval string parser.

Used by both system cron and employee cron (automation.py).
"""
from __future__ import annotations


def parse_interval(interval_str: str | None) -> int | None:
    """Parse interval string like '5m', '1h', '30s', '1d' to seconds.

    Returns None if the string is invalid or empty.
    """
    if not interval_str:
        return None
    interval_str = str(interval_str).strip().lower()
    if not interval_str:
        return None
    unit = interval_str[-1]
    try:
        value = int(interval_str[:-1])
    except ValueError:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    mult = multipliers.get(unit)
    if mult is None:
        return None
    return value * mult
