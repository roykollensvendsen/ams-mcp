# SPDX-License-Identifier: GPL-3.0-or-later
"""What the meter reader's JSON pages say, with their short keys given names.

The keys are the firmware's own, read from AmsToMqttBridge's source at the
version pinned in FIRMWARE (UtilitechAS/amsreader-firmware, tag v2.4.11,
lib/SvelteUi/json/*.json and lib/SvelteUi/src/AmsWebServer.cpp). A reader on
another version may name things differently; docs/pages.md has the whole map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The firmware version the key map was read from.
FIRMWARE = "v2.4.11"
#: The value tariff.json puts after the last real capacity step.
NO_UPPER_STEP = 65535
#: data.json "hm", the reader's own view of the meter: 1 under 15 s since the
#: last reading, 2 under 30 s, 3 older or a fault, 0 not yet read since boot.
HAN_FRESH = (1, 2)


class PageError(ValueError):
    """A page did not have the shape the pinned firmware gives it."""


@dataclass(frozen=True)
class Phase:
    """One phase: L1, L2 or L3."""

    name: str
    volts: float
    amps: float
    #: The meter's own figure for the phase, None when it reports none.
    import_w: int | None
    export_w: int | None
    power_factor: float | None


@dataclass(frozen=True)
class Spent:
    """Energy over a span: in and out of the house, and what it cost or earned."""

    import_kwh: float
    export_kwh: float
    cost: float
    income: float


@dataclass(frozen=True)
class Live:
    """data.json: the meter's latest reading and the reader's running totals."""

    import_w: int
    export_w: int
    power_factor: float | None
    phases: tuple[Phase, ...]
    hour: Spent
    today: Spent
    month: Spent
    #: Capacity: the average of the month's top hours, in kWh (kW over an hour).
    month_max_kwh: float
    top_hours_kwh: tuple[float, ...]
    step_kw: int
    price: float | None
    currency: str
    han_status: int
    device_time: int

    @property
    def fresh(self) -> bool:
        """Whether the reader heard from the meter within about 30 seconds."""
        return self.han_status in HAN_FRESH


@dataclass(frozen=True)
class Peak:
    """One of the month's top hours: the day of the month, and the kWh in that hour."""

    day: int
    kwh: float


@dataclass(frozen=True)
class Tariff:
    """tariff.json: the capacity steps and the month's top hours."""

    steps_kw: tuple[int, ...]
    peaks: tuple[Peak, ...]
    step_kw: int
    month_max_kwh: float


@dataclass(frozen=True)
class Prices:
    """energyprice.json: the price from this hour on, by hours from now."""

    currency: str
    #: Index 0 is this hour, 1 the next, and so on; None where the reader has no price.
    by_hour: tuple[float | None, ...]


@dataclass(frozen=True)
class Hours:
    """dayplot.json: energy per hour, indexed by the UTC hour it started at."""

    import_kwh: tuple[float, ...]
    export_kwh: tuple[float, ...]


@dataclass(frozen=True)
class Days:
    """monthplot.json: energy per day, indexed by the local day of the month, 1 to 31."""

    import_kwh: dict[int, float]
    export_kwh: dict[int, float]


@dataclass(frozen=True)
class System:
    """sysinfo.json, only the parts this plugin uses: never an address or an id."""

    version: str
    last_month: Spent | None


def _number(page: dict[str, Any], key: str) -> float:
    value = page.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key!r} is {value!r}, not a number"
        raise PageError(msg)
    return float(value)


def _table(page: dict[str, Any], key: str) -> dict[str, Any]:
    value = page.get(key)
    if not isinstance(value, dict):
        msg = f"{key!r} is not a table"
        raise PageError(msg)
    return value


def _spent(table: dict[str, Any]) -> Spent:
    # "u" used (imported), "c" its cost, "p" produced (exported), "i" its income.
    return Spent(_number(table, "u"), _number(table, "p"), _number(table, "c"), _number(table, "i"))


def _phase(name: str, table: dict[str, Any]) -> Phase:
    volts, amps = _number(table, "u"), _number(table, "i")
    imp, exp, pf = int(_number(table, "p")), int(_number(table, "q")), _number(table, "f")
    # A meter that sends no per-phase power or factor leaves them at zero while current flows.
    silent = imp == 0 and exp == 0 and amps > 0
    return Phase(name, volts, amps, None if silent else imp, None if silent else exp, None if pf == 0 else pf)


def live(page: dict[str, Any]) -> Live:
    """data.json, named."""
    accounting = _table(page, "ea")
    peaks = accounting.get("p")
    if not isinstance(peaks, list):
        msg = "'ea.p' is not a list"
        raise PageError(msg)
    phases = tuple(_phase(n.upper(), page[n]) for n in ("l1", "l2", "l3") if isinstance(page.get(n), dict))
    import_w, pf = int(_number(page, "i")), _number(page, "f")
    price = page.get("p")
    return Live(
        import_w=import_w,
        export_w=int(_number(page, "e")),
        power_factor=None if pf == 0 else pf,
        phases=phases,
        hour=_spent(_table(accounting, "h")),
        today=_spent(_table(accounting, "d")),
        month=_spent(_table(accounting, "m")),
        month_max_kwh=_number(accounting, "x"),
        top_hours_kwh=tuple(float(v) for v in peaks),
        step_kw=int(_number(accounting, "t")),
        price=float(price) if isinstance(price, int | float) and page.get("pe") is not False else None,
        currency=str(page.get("pc") or ""),
        han_status=int(_number(page, "hm")),
        device_time=int(_number(page, "c")),
    )


def tariff(page: dict[str, Any]) -> Tariff:
    """tariff.json, named."""
    steps, peaks = page.get("t"), page.get("p")
    if not isinstance(steps, list) or not isinstance(peaks, list):
        msg = "'t' or 'p' is not a list"
        raise PageError(msg)
    return Tariff(
        steps_kw=tuple(int(s) for s in steps if 0 < int(s) < NO_UPPER_STEP),
        peaks=tuple(Peak(int(_number(p, "d")), _number(p, "v")) for p in peaks if _number(p, "d") > 0),
        step_kw=int(_number(page, "c")),
        month_max_kwh=_number(page, "m"),
    )


def prices(page: dict[str, Any]) -> Prices:
    """energyprice.json, named: "00" is this hour, "01" the next, up to "35"."""
    by_hour: list[float | None] = []
    for offset in range(36):
        value = page.get(f"{offset:02d}")
        by_hour.append(float(value) if isinstance(value, int | float) else None)
    return Prices(str(page.get("currency") or ""), tuple(by_hour))


def hours(page: dict[str, Any]) -> Hours:
    """dayplot.json, named: "i00" to "i23" in and "e00" to "e23" out, by UTC hour."""
    return Hours(
        tuple(_number(page, f"i{h:02d}") for h in range(24)),
        tuple(_number(page, f"e{h:02d}") for h in range(24)),
    )


def days(page: dict[str, Any]) -> Days:
    """monthplot.json, named: "i01" to "i31" in and "e01" to "e31" out, by local day."""
    return Days(
        {d: _number(page, f"i{d:02d}") for d in range(1, 32)},
        {d: _number(page, f"e{d:02d}") for d in range(1, 32)},
    )


def system(page: dict[str, Any]) -> System:
    """sysinfo.json, only its version and last month's totals."""
    last = page.get("last_month")
    return System(str(page.get("version") or ""), _spent(last) if isinstance(last, dict) else None)
