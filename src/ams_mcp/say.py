# SPDX-License-Identifier: GPL-3.0-or-later
"""Numbers as the voice reads them: kW, kWh and kr, rounded for the ear."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: Where a figure stops needing decimals to be read aloud.
WHOLE = 100
TENTHS = 10


def rounded(value: float, places: int) -> str:
    """Rounded half up, as a person would: 70.85 is "70.9", where float formatting says "70.8"."""
    step = Decimal(1).scaleb(-places)
    return str(Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP))


def kw(watts: float) -> str:
    """Watts as kW with two decimals: 1048 is "1.05"."""
    return rounded(watts / 1000, 2)


def kwh(value: float) -> str:
    """Energy: two decimals under 10, one under 100, whole above."""
    if value >= WHOLE:
        return rounded(value, 0)
    if value >= TENTHS:
        return rounded(value, 1)
    return rounded(value, 2)


def money(value: float) -> str:
    """An amount: two decimals under 100, whole above."""
    return rounded(value, 0) if abs(value) >= WHOLE else rounded(value, 2)


def currency(code: str) -> str:
    """Kroner as "kr"; any other currency by its code."""
    return "kr" if code in {"NOK", "SEK", "DKK", ""} else code


def ordinal(day: int) -> str:
    """The 1st, the 2nd, the 23rd."""
    teen = 11 <= day % 100 <= 13  # noqa: PLR2004 - 11th to 13th break the pattern
    suffix = "th" if teen else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def listing(items: list[str]) -> str:
    """Join words as said: "a", "a and b", "a, b and c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
