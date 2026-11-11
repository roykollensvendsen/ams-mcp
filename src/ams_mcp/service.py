# SPDX-License-Identifier: GPL-3.0-or-later
"""The five questions, answered from the reader's pages.

Every answer has 'success', a 'say' text to read aloud as it is, 'read_at'
(when the reading was taken, in local time) and 'confirmed'. A reading older
than about half a minute, or one the reader says is stale, is said as "Not
confirmed" first. When the reader does not answer, nothing is guessed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import TYPE_CHECKING, Any

from ams_mcp import pages, say
from ams_mcp.pages import FIRMWARE, PageError
from ams_mcp.reader import ReaderError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ams_mcp.reader import Page, Source

#: Seconds after which a reading is no longer said as current.
STALE_AFTER = 30.0
#: How many hours ahead the price answer lists.
NEXT_HOURS = 12
#: How many hours ahead the price answer reads aloud, and how many cheap hours.
SPOKEN_HOURS = 3

UNREACHABLE = "The meter reader did not answer. Check that it has power and is on the Wi-Fi, then ask again."
UNKNOWN = (
    "The meter reader answered in a shape this plugin does not know; "
    f"it was written for firmware {FIRMWARE}. Check the reader's firmware version."
)
NO_PRICES = "The meter reader has no prices just now. Check its price source in the reader's own settings."
PERIODS = ("day", "month")


class AmsService:
    """Answers about the meter, from a source of pages: the real reader or the simulator."""

    def __init__(
        self,
        source: Source,
        *,
        timezone: dt.tzinfo,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Answers from this source, with days and hours counted in this time zone."""
        self._source, self._tz, self._clock = source, timezone, clock

    # The five tools

    async def now(self) -> dict[str, Any]:
        """Power now, per phase, and the power factor."""
        return await self._answer(self._now)

    async def energy(self) -> dict[str, Any]:
        """Energy in and out this hour, today and this month."""
        return await self._answer(self._energy)

    async def price(self) -> dict[str, Any]:
        """The price now, the next hours, and the cheapest hours left today."""
        return await self._answer(self._price)

    async def capacity(self) -> dict[str, Any]:
        """The capacity step, the month's top hours, and how far this hour is from the next step."""
        return await self._answer(self._capacity)

    async def history(self, period: str = "day") -> dict[str, Any]:
        """A short summary of today by hour, or of this month by day."""
        if period not in PERIODS:
            return {"success": False, "say": f"I can sum up a day or month, not {period!r}."}
        return await self._answer(self._day if period == "day" else self._month)

    # How every answer is made

    async def _answer(
        self, work: Callable[[], Awaitable[tuple[dict[str, Any], list[Page]]]]
    ) -> dict[str, Any]:
        try:
            answer, read = await work()
        except ReaderError:
            # RULE: a reader that does not answer is said, and nothing is guessed
            return {"success": False, "say": UNREACHABLE}
        except (PageError, KeyError, TypeError, ValueError):
            return {"success": False, "say": UNKNOWN}
        if not answer.get("success", True):
            return answer
        oldest = min(p.read_at for p in read)
        age = self._clock() - oldest
        doubt = None
        # RULE: a reading older than about half a minute is not confirmed
        if age > STALE_AFTER:
            doubt = f"this reading is {_age(age)} old."
        elif any(p.body.get("hm") is not None and int(p.body["hm"]) not in pages.HAN_FRESH for p in read):
            doubt = "the reader has not heard from the meter for over 30 seconds."
        if doubt:
            answer["say"] = f"Not confirmed: {doubt} {answer['say']}"
        return {
            "success": True,
            **answer,
            "read_at": self._local(oldest).isoformat(timespec="seconds"),
            "confirmed": doubt is None,
        }

    async def _pages(self, *names: str) -> list[Page]:
        return list(await asyncio.gather(*(self._source.page(n) for n in names)))

    async def _maybe(self, name: str) -> Page | None:
        """A page an answer can do without, such as the temperatures."""
        try:
            return await self._source.page(name)
        except (ReaderError, PageError):
            return None

    def _local(self, timestamp: float) -> dt.datetime:
        return dt.datetime.fromtimestamp(timestamp, self._tz)

    # The answers themselves

    async def _now(self) -> tuple[dict[str, Any], list[Page]]:
        data, temps = await asyncio.gather(self._source.page("data.json"), self._maybe("temperature.json"))
        live = pages.live(data.body)
        if live.export_w > live.import_w:
            words = [f"Sending {say.kw(live.export_w)} kW to the grid now."]
        else:
            words = [f"Drawing {say.kw(live.import_w)} kW from the grid now."]
        amps = [say.rounded(p.amps, 1) for p in live.phases]
        if amps:
            words.append(f"The phases carry {say.listing(amps)} A.")
        if live.power_factor is None:
            words.append("This meter sends no power factor.")
        else:
            words.append(f"Power factor {say.rounded(live.power_factor, 2)}.")
        answer = {
            "say": " ".join(words),
            "import_kw": live.import_w / 1000,
            "export_kw": live.export_w / 1000,
            "power_factor": live.power_factor,
            "phases": [
                {
                    "phase": p.name,
                    "volts": p.volts,
                    "amps": p.amps,
                    "import_kw": None if p.import_w is None else p.import_w / 1000,
                    "export_kw": None if p.export_w is None else p.export_w / 1000,
                    "power_factor": p.power_factor,
                }
                for p in live.phases
            ],
            "temperatures": (
                [{"sensor": s.index, "celsius": s.celsius} for s in pages.sensors(temps.body)]
                if temps
                else []
            ),
        }
        return answer, [data]

    async def _energy(self) -> tuple[dict[str, Any], list[Page]]:
        data = await self._source.page("data.json")
        live = pages.live(data.body)
        kr = say.currency(live.currency)

        def spoken(name: str, spent: pages.Spent, *, cost: bool) -> str:
            text = f"{name} {say.kwh(spent.import_kwh)} kWh"
            if spent.export_kwh > 0:
                text += f", {say.kwh(spent.export_kwh)} kWh out"
            if cost:
                text += f", {say.money(spent.cost)} {kr}"
            return text + "."

        answer = {
            "say": " ".join(
                [
                    spoken("Today", live.today, cost=True),
                    spoken("This hour", live.hour, cost=False),
                    spoken("This month", live.month, cost=True),
                ]
            ),
            "hour": _spent(live.hour),
            "today": _spent(live.today),
            "month": _spent(live.month),
            "currency": live.currency,
        }
        return answer, [data]

    async def _price(self) -> tuple[dict[str, Any], list[Page]]:
        page = await self._source.page("energyprice.json")
        found = pages.prices(page.body)
        if found.by_hour[0] is None:
            return {"success": False, "say": NO_PRICES}, [page]
        kr = say.currency(found.currency)
        start = self._local(page.read_at).replace(minute=0, second=0, microsecond=0)

        def at(offset: int) -> dict[str, Any]:
            hour = start + dt.timedelta(hours=offset)
            return {"hour": hour.strftime("%H:%M"), "price": found.by_hour[offset]}

        known = [o for o, v in enumerate(found.by_hour) if v is not None]
        upcoming = [at(o) for o in known if 0 < o <= NEXT_HOURS]
        today = [o for o in known if (start + dt.timedelta(hours=o)).date() == start.date()]
        cheapest = [at(o) for o in sorted(today, key=lambda o: (found.by_hour[o], o))[:SPOKEN_HOURS]]
        words = [f"{say.rounded(found.by_hour[0], 2)} {kr} per kWh now."]
        if upcoming:
            ahead = [say.rounded(h["price"], 2) for h in upcoming[:SPOKEN_HOURS]]
            count = {1: "hour", 2: "two hours", 3: "three hours"}[len(ahead)]
            words.append(f"The next {count} {say.listing(ahead)} {kr}.")
        spoken = [f"{c['hour']} at {say.rounded(c['price'], 2)}" for c in cheapest]
        words.append(f"Cheapest left today: {say.listing(spoken)} {kr}.")
        answer = {
            "say": " ".join(words),
            "now": at(0),
            "next": upcoming,
            "cheapest_today": cheapest,
            "currency": found.currency,
        }
        return answer, [page]

    async def _capacity(self) -> tuple[dict[str, Any], list[Page]]:
        data, tariff_page = await self._pages("data.json", "tariff.json")
        live, tariff = pages.live(data.body), pages.tariff(tariff_page.body)
        moment = self._local(data.read_at)
        top = (live.top_hours_kwh and len(live.top_hours_kwh)) or len(tariff.peaks) or 3
        lower = [s for s in tariff.steps_kw if s < tariff.step_kw]
        highest = tariff.step_kw not in tariff.steps_kw
        step = {
            "from_kw": max(tariff.steps_kw) if highest else (max(lower) if lower else 0),
            "to_kw": None if highest else tariff.step_kw,
        }
        hour_left = 1 - (moment.minute * 60 + moment.second) / 3600
        projected = live.hour.import_kwh + live.import_w / 1000 * hour_left
        needed = None if highest else _needed(tariff, top, moment.day)
        peaks = [f"{say.kwh(p.kwh)} on the {say.ordinal(p.day)}" for p in tariff.peaks]
        if highest:
            words = [f"Capacity step over {step['from_kw']} kW, the highest step."]
        else:
            words = [f"Capacity step {step['from_kw']} to {step['to_kw']} kW."]
        count = {1: "top hour", 2: "top two hours", 3: "top three hours"}.get(len(peaks), "top hours")
        if peaks:
            words.append(
                f"Your {count} average {say.rounded(tariff.month_max_kwh, 2)} kW: {say.listing(peaks)}."
            )
        words.append(
            f"This hour has used {say.kwh(live.hour.import_kwh)} kWh, and at this rate ends near "
            f"{say.kwh(projected)}."
        )
        if needed is not None:
            words.append(f"It would take over {say.kwh(needed)} kWh this hour to reach the next step.")
        answer = {
            "say": " ".join(words),
            "step": step,
            "month_average_kw": tariff.month_max_kwh,
            "top_hours": [{"day": p.day, "kwh": p.kwh} for p in tariff.peaks],
            "this_hour_kwh": live.hour.import_kwh,
            "projected_hour_kwh": round(projected, 3),
            "next_step_after_kwh": None if needed is None else round(needed, 3),
        }
        return answer, [data, tariff_page]

    async def _day(self) -> tuple[dict[str, Any], list[Page]]:
        data, plot = await self._pages("data.json", "dayplot.json")
        live, by_utc = pages.live(data.body), pages.hours(plot.body)
        kr = say.currency(live.currency)
        top = self._local(plot.read_at).replace(minute=0, second=0, microsecond=0)
        # The plot keeps the last 24 full hours, each in the slot of the UTC hour it began.
        hours = []
        for back in range(24, 0, -1):
            start = top - dt.timedelta(hours=back)
            slot = start.astimezone(dt.UTC).hour
            hours.append((start, by_utc.import_kwh[slot], by_utc.export_kwh[slot]))
        today = [h for h in hours if h[0].date() == top.date()]
        words = [f"Today so far {say.kwh(live.today.import_kwh)} kWh, {say.money(live.today.cost)} {kr}."]
        span = today or hours
        if span:
            busy = max(span, key=lambda h: h[1])
            calm = min(span, key=lambda h: h[1])
            when = "" if today else " over the last day"
            words.append(
                f"Busiest hour{when} {busy[0]:%H:%M} with {say.kwh(busy[1])} kWh, "
                f"quietest {calm[0]:%H:%M} with {say.kwh(calm[1])} kWh."
            )
        answer = {
            "say": " ".join(words),
            "period": "day",
            "today": _spent(live.today),
            "hours": [
                {"start": s.isoformat(timespec="seconds"), "import_kwh": i, "export_kwh": e}
                for s, i, e in hours
            ],
            "currency": live.currency,
        }
        return answer, [data, plot]

    async def _month(self) -> tuple[dict[str, Any], list[Page]]:
        (data, plot), info = await asyncio.gather(
            self._pages("data.json", "monthplot.json"), self._maybe("sysinfo.json")
        )
        live, by_day = pages.live(data.body), pages.days(plot.body)
        kr = say.currency(live.currency)
        today = self._local(plot.read_at).day
        # Days before today are this month's; from today on the plot still holds last month's.
        days = [(d, by_day.import_kwh[d], by_day.export_kwh[d]) for d in range(1, today)]
        words = [
            f"This month so far {say.kwh(live.month.import_kwh)} kWh, {say.money(live.month.cost)} {kr}."
        ]
        if days:
            busy = max(days, key=lambda d: d[1])
            calm = min(days, key=lambda d: d[1])
            words.append(
                f"Busiest day the {say.ordinal(busy[0])} with {say.kwh(busy[1])} kWh, "
                f"quietest the {say.ordinal(calm[0])} with {say.kwh(calm[1])} kWh."
            )
        last = pages.system(info.body).last_month if info else None
        if last:
            words.append(f"Last month {say.kwh(last.import_kwh)} kWh, {say.money(last.cost)} {kr}.")
        answer = {
            "say": " ".join(words),
            "period": "month",
            "month": _spent(live.month),
            "days": [{"day": d, "import_kwh": i, "export_kwh": e} for d, i, e in days],
            "last_month": _spent(last) if last else None,
            "currency": live.currency,
        }
        return answer, [data, plot]


def _spent(spent: pages.Spent) -> dict[str, float]:
    return {
        "import_kwh": spent.import_kwh,
        "export_kwh": spent.export_kwh,
        "cost": spent.cost,
        "income": spent.income,
    }


def _needed(tariff: pages.Tariff, top: int, today: int) -> float:
    """The kWh this hour would need to lift the month's average over the present step.

    The month's top hours are one per day, so an hour today replaces today's own
    entry rather than pushing out another day's.
    """
    others = sorted((p.kwh for p in tariff.peaks if p.day != today), reverse=True)
    counted = min(top, len(others) + 1)
    return counted * tariff.step_kw - sum(others[: counted - 1])


def _age(seconds: float) -> str:
    minutes = 120
    if seconds < minutes:
        return f"{int(seconds)} seconds"
    return f"{int(seconds // 60)} minutes"
