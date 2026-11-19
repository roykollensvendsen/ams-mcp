# SPDX-License-Identifier: GPL-3.0-or-later
"""The five questions, answered from the reader's pages.

Every answer has 'success', a 'say' text to read aloud as it is, 'read_at'
(when the reading was taken, in local time) and 'confirmed'. A reading older
than about half a minute, or one the reader says is stale, is said as "Not
confirmed" first. When the reader does not answer, nothing is guessed.
"""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import re
import time
from typing import TYPE_CHECKING, Any

from ams_mcp import pages, say
from ams_mcp.pages import FIRMWARE, PageError
from ams_mcp.reader import NameNotFoundError, ReaderError, StrangeAnswerError
from ams_mcp.settings import SettingsError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ams_mcp.reader import Page, Source

#: Seconds after which a reading is no longer said as current.
STALE_AFTER = 30.0
#: How many hours ahead the price answer lists.
NEXT_HOURS = 12
#: How many hours ahead the price answer reads aloud, and how many cheap hours.
SPOKEN_HOURS = 3
#: How long a run of hours in a row the price answer finds: a dishwasher, a wash, a charge.
RUN_HOURS = range(1, 7)
#: A time to end a run by: two-digit hour and minute, as "07:00".
UNTIL = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
#: The first day of a month its course is guessed on: before it, one whole day at most.
COURSE_FROM_DAY = 3

#: Every failure says what is wrong and what to do next; {where} is the reader's address.
UNREACHABLE = (
    "The meter reader at {where} doesn't answer. Check that it has power and is on the Wi-Fi, then ask again."
)
NOT_FOUND = (
    "The meter reader's name {where} isn't known on the home network. Check that the router still lists "
    "the reader, or write its IP address as [reader] address in ams-mcp's settings file. "
    "Then restart the plugin."
)
UNKNOWN = (
    "The meter reader at {where} answered in a shape this plugin doesn't know; it was written for firmware "
    f"{FIRMWARE}. Open http://{{where}}/status and check the reader's firmware version."
)
LOGIN = (
    "The meter reader at {where} asks for a login, and this plugin never sends one. "
    "Open http://{where}/configuration and let the data pages be read without a login."
)
NO_PRICES = (
    "The meter reader at {where} has no prices just now. "
    "Open http://{where}/priceconfig and check its price source."
)
PERIODS = ("day", "month")


class AmsService:
    """Answers about the meter, from a source of pages: the real reader or the simulator."""

    def __init__(
        self,
        source: Source,
        *,
        timezone: dt.tzinfo,
        where: str = "its address",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Answers from this source, with days and hours in this time zone; where names the reader."""
        self._source, self._tz, self._clock, self._where = source, timezone, clock, where

    # The five tools

    async def now(self) -> dict[str, Any]:
        """Power now, per phase, and the power factor."""
        return await self._answer(self._now)

    async def energy(self) -> dict[str, Any]:
        """Energy in and out this hour, today and this month."""
        return await self._answer(self._energy)

    async def price(self, hours: int | None = None, until: str | None = None) -> dict[str, Any]:
        """The price now, the next hours, and the cheapest hours left today; or the cheapest run of hours.

        With `until`, the run may start any hour from now on, and ends by that time.
        """
        refused = _run_refused(hours, until)
        if refused:
            return {"success": False, "say": refused}
        return await self._answer(lambda: self._price(hours, until))

    async def capacity(self) -> dict[str, Any]:
        """The capacity step, the month's top hours, and how far this hour is from the next step."""
        return await self._answer(self._capacity)

    async def history(self, period: str = "day") -> dict[str, Any]:
        """A short summary of today by hour, or of this month by day."""
        if period not in PERIODS:
            return {
                "success": False,
                "say": f"I can sum up a day or month, not {period!r}. Ask for the day or the month.",
            }
        return await self._answer(self._day if period == "day" else self._month)

    # How every answer is made

    async def _answer(
        self, work: Callable[[], Awaitable[tuple[dict[str, Any], list[Page]]]]
    ) -> dict[str, Any]:
        try:
            answer, read = await work()
        except SettingsError as error:
            return {
                "success": False,
                "say": f"ams-mcp can't read its settings. {error} Then restart the plugin.",
            }
        except StrangeAnswerError as error:
            # RULE: a reader that answers wrongly is not blamed on its power or wifi
            return {"success": False, "say": (LOGIN if error.login else UNKNOWN).format(where=self._where)}
        except ReaderError as error:
            # RULE: a reader that does not answer is said, and nothing is guessed
            return {"success": False, "say": _unreachable(error).format(where=self._where)}
        except (PageError, KeyError, TypeError, ValueError):
            return {"success": False, "say": UNKNOWN.format(where=self._where)}
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
        """A page an answer can do without, such as last month's totals."""
        try:
            return await self._source.page(name)
        except (ReaderError, PageError):
            return None

    def _local(self, timestamp: float) -> dt.datetime:
        return dt.datetime.fromtimestamp(timestamp, self._tz)

    def _later(self, moment: dt.datetime, hours: int) -> dt.datetime:
        """The local time this many real hours on, the clock changes counted."""
        # RULE: hours are counted in real hours, not on the clock
        return (moment.astimezone(dt.UTC) + dt.timedelta(hours=hours)).astimezone(self._tz)

    # The answers themselves

    async def _now(self) -> tuple[dict[str, Any], list[Page]]:
        data = await self._source.page("data.json")
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

    async def _price(
        self, hours: int | None = None, until: str | None = None
    ) -> tuple[dict[str, Any], list[Page]]:
        page = await self._source.page("energyprice.json")
        found = pages.prices(page.body)
        if found.by_hour[0] is None:
            return {"success": False, "say": NO_PRICES.format(where=self._where)}, [page]
        kr = say.currency(found.currency)
        start = self._local(page.read_at).replace(minute=0, second=0, microsecond=0)

        def at(offset: int) -> dict[str, Any]:
            hour = self._later(start, offset)
            return {"hour": hour.strftime("%H:%M"), "price": found.by_hour[offset]}

        known = [o for o, v in enumerate(found.by_hour) if v is not None]
        upcoming = [at(o) for o in known if 0 < o <= NEXT_HOURS]
        today = [o for o in known if self._later(start, o).date() == start.date()]
        cheapest = [at(o) for o in sorted(today, key=lambda o: (found.by_hour[o], o))[:SPOKEN_HOURS]]
        words = [f"{say.rounded(found.by_hour[0], 2)} {kr} per kWh now."]
        if upcoming:
            ahead = [say.rounded(h["price"], 2) for h in upcoming[:SPOKEN_HOURS]]
            count = {1: "hour", 2: "two hours", 3: "three hours"}[len(ahead)]
            words.append(f"The next {count} {say.listing(ahead)} {kr}.")
        spoken = [f"{c['hour']} at {say.rounded(c['price'], 2)}" for c in cheapest]
        words.append(f"Cheapest left today: {say.listing(spoken)} {kr}.")
        answer: dict[str, Any] = {
            "say": " ".join(words),
            "now": at(0),
            "next": upcoming,
            "cheapest_today": cheapest,
            "currency": found.currency,
        }
        if hours is None:
            return answer, [page]
        return self._with_run(answer, found.by_hour, start, (hours, until, kr), page.read_at), [page]

    def _with_run(
        self,
        answer: dict[str, Any],
        by_hour: tuple[float | None, ...],
        start: dt.datetime,
        asked: tuple[int, str | None, str],
        read_at: float,
    ) -> dict[str, Any]:
        """The price answer with the run asked for, or a refusal when `until` is a time the clocks skip."""
        hours, until, kr = asked
        if until is None:
            answer["run"], answer["say"] = self._run(by_hour, start, hours, kr)
            return answer
        deadline = self._deadline(self._local(read_at), until)
        if deadline is None:
            return {
                "success": False,
                "say": f"There is no {until} tonight, since the clocks go forward. Ask for another time.",
            }
        answer["run"], answer["say"] = self._run_until(by_hour, start, (hours, kr), deadline, until)
        return answer

    def _run(
        self, by_hour: tuple[float | None, ...], start: dt.datetime, hours: int, kr: str
    ) -> tuple[dict[str, Any] | None, str]:
        """The cheapest `hours` in a row that start today, and the sentence that says it.

        A run may go past midnight once tomorrow's prices are out; the page has them
        from about 13:00. Slots are real hours from now, so a clock change is counted.
        """
        today = [o for o in range(len(by_hour)) if self._later(start, o).date() == start.date()]
        not_out = _not_out_yet(by_hour, tomorrow=len(today))
        fits = _runs(by_hour, today, hours)
        if not fits:
            lead = "Tomorrow's prices aren't out yet, and n" if not_out else "N"
            return None, f"{lead}o {_run_of(hours)} {'is' if hours == 1 else 'are'} known from now."
        run, cost = self._cheapest(by_hour, start, fits, (hours, kr))
        return run, _run_said(run, cost, not_out=not_out)

    def _run_until(
        self,
        by_hour: tuple[float | None, ...],
        start: dt.datetime,
        asked: tuple[int, str],
        deadline: dt.datetime,
        until: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """The cheapest run of `asked` hours that ends by `deadline`, from any hour on; and its sentence.

        With fewer hours in a row priced before then, it is the longest run there is, and says so.
        """
        hours, kr = asked
        ends = [self._later(start, o).timestamp() for o in range(len(by_hour) + 1)]
        # RULE: a run asked to end by a time never ends after it
        last = max(o for o, end in enumerate(ends) if end <= deadline.timestamp())
        if last == 0:
            return None, f"No whole hour is left before {until}."
        today = [o for o in range(len(by_hour)) if self._later(start, o).date() == start.date()]
        not_out = last > len(today) and _not_out_yet(by_hour, tomorrow=len(today))
        length = next(n for n in range(hours, 0, -1) if _runs(by_hour[:last], list(range(last)), n))
        run, cost = self._cheapest(
            by_hour, start, _runs(by_hour[:last], list(range(last)), length), (length, kr)
        )
        said = _run_said(run, cost, not_out=not_out)
        if length < hours:
            priced = "1 hour is" if length == 1 else f"{length} hours in a row are"
            said = f"Only {priced} priced before {until}. {said}"
        return run, said

    def _cheapest(
        self, by_hour: tuple[float | None, ...], start: dt.datetime, fits: list[int], asked: tuple[int, str]
    ) -> tuple[dict[str, Any], str]:
        """The cheapest of the runs starting at `fits`, the earliest on a tie, and its cost as said."""
        hours, kr = asked
        best = min(fits, key=lambda o: (sum(by_hour[o : o + hours]), o))  # type: ignore[arg-type]
        price = round(sum(by_hour[best : best + hours]) / hours, 4)  # type: ignore[arg-type]
        begins, ends = (self._later(start, o).strftime("%H:%M") for o in (best, best + hours))
        run = {"hours": hours, "start": begins, "end": ends, "price": price}
        return run, f"{say.rounded(price, 2)} {kr} per kWh"

    def _deadline(self, now: dt.datetime, until: str) -> dt.datetime | None:
        """The next `until` after now, this very minute counting as gone; None if the clocks skip it."""
        at = dt.time.fromisoformat(until)
        day = now.date() + dt.timedelta(days=at <= now.time().replace(second=0, microsecond=0))
        moment = dt.datetime.combine(day, at, tzinfo=self._tz)
        real = moment.astimezone(dt.UTC).astimezone(self._tz)
        return moment if real.time() == at else None

    async def _capacity(self) -> tuple[dict[str, Any], list[Page]]:
        data, tariff_page = await self._pages("data.json", "tariff.json")
        live, tariff = pages.live(data.body), pages.tariff(tariff_page.body)
        moment = self._local(data.read_at)
        hour_left = 1 - (moment.minute * 60 + moment.second) / 3600
        projected = live.hour.import_kwh + live.import_w / 1000 * hour_left
        step = _step(tariff)
        top = (live.top_hours_kwh and len(live.top_hours_kwh)) or len(tariff.peaks) or 3
        needed = None if step["to_kw"] is None else _needed(tariff, top, moment.day)
        answer = {
            "say": " ".join(_capacity_words(tariff, step, live.hour.import_kwh, projected, needed)),
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
            start = self._later(top, -back)
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
        on = self._local(plot.read_at).date()
        today = on.day
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
        course, said = _course([i for _, i, _ in days], on, last)
        words.append(said)
        answer = {
            "say": " ".join(words),
            "period": "month",
            "month": _spent(live.month),
            "days": [{"day": d, "import_kwh": i, "export_kwh": e} for d, i, e in days],
            "last_month": _spent(last) if last else None,
            "course": course,
            "currency": live.currency,
        }
        return answer, [data, plot]


def _course(
    whole_days: list[float], on: dt.date, last: pages.Spent | None
) -> tuple[dict[str, Any] | None, str]:
    """Where the month is headed, on a straight line from its whole days so far, and how to say it.

    Today is left out: a part of a day would drag the line down early in the day.
    It is a guess, and said as one.
    """
    # RULE: a month's course is not guessed before the third day
    if on.day < COURSE_FROM_DAY or not whole_days:
        return None, "Too early in the month to say where it will end."
    month_kwh = sum(whole_days) / len(whole_days) * calendar.monthrange(on.year, on.month)[1]
    more = None if last is None else month_kwh - last.import_kwh
    course = {
        "whole_days": len(whole_days),
        "month_kwh": round(month_kwh, 1),
        "more_than_last_month_kwh": None if more is None else round(more, 1),
    }
    head = f"On course for about {say.kwh(month_kwh)} kWh this month"
    if more is None:
        return course, (
            f"{head}, going by the whole days so far. "
            "Last month's total isn't known, so there is nothing to compare it with."
        )
    than = f"{say.kwh(abs(more))} kWh {'more' if more >= 0 else 'less'} than last month"
    return course, f"{head}, {than}, going by the whole days so far."


def _spent(spent: pages.Spent) -> dict[str, float]:
    return {
        "import_kwh": spent.import_kwh,
        "export_kwh": spent.export_kwh,
        "cost": spent.cost,
        "income": spent.income,
    }


def _step(tariff: pages.Tariff) -> dict[str, int | None]:
    """The capacity step the month is in: from the edge below to its own edge, or None above the top."""
    if tariff.step_kw not in tariff.steps_kw:
        return {"from_kw": max(tariff.steps_kw, default=0), "to_kw": None}
    lower = [s for s in tariff.steps_kw if s < tariff.step_kw]
    return {"from_kw": max(lower, default=0), "to_kw": tariff.step_kw}


def _capacity_words(
    tariff: pages.Tariff, step: dict[str, int | None], used: float, projected: float, needed: float | None
) -> list[str]:
    """The capacity answer, sentence by sentence."""
    if step["to_kw"] is None:
        words = [f"Capacity step over {step['from_kw']} kW, the highest step."]
    else:
        words = [f"Capacity step {step['from_kw']} to {step['to_kw']} kW."]
    peaks = [f"{say.kwh(p.kwh)} on the {say.ordinal(p.day)}" for p in tariff.peaks]
    count = {1: "top hour", 2: "top two hours", 3: "top three hours"}.get(len(peaks), "top hours")
    if peaks:
        words.append(f"Your {count} average {say.rounded(tariff.month_max_kwh, 2)} kW: {say.listing(peaks)}.")
    words.append(
        f"This hour has used {say.kwh(used)} kWh, and at this rate ends near {say.kwh(projected)} kWh."
    )
    if needed is not None:
        words.append(f"It would take over {say.kwh(needed)} kWh this hour to reach the next step.")
    return words


def _needed(tariff: pages.Tariff, top: int, today: int) -> float:
    """The kWh this hour would need to lift the month's average over the present step.

    The month's top hours are one per day, so an hour today replaces today's own
    entry rather than pushing out another day's.
    """
    others = sorted((p.kwh for p in tariff.peaks if p.day != today), reverse=True)
    counted = min(top, len(others) + 1)
    return counted * tariff.step_kw - sum(others[: counted - 1])


def _run_refused(hours: int | None, until: str | None) -> str | None:
    """Why a run cannot be looked for as asked, or None when it can."""
    if hours is not None and hours not in RUN_HOURS:
        return f"I can find 1 to 6 hours in a row, not {hours}. Ask for 1 to 6 hours."
    if until is None:
        return None
    if hours is None:
        return "Say how many hours in a row too, 1 to 6, with the time to end by."
    if not UNTIL.fullmatch(until):
        return f"I need the time to end by as HH:MM, like 07:00, not {until}."
    return None


def _not_out_yet(by_hour: tuple[float | None, ...], *, tomorrow: int) -> bool:
    """Whether tomorrow's prices are not out yet: no price anywhere from its first slot on.

    A gap with prices after it is just a gap, and says nothing about tomorrow.
    """
    return tomorrow < len(by_hour) and all(p is None for p in by_hour[tomorrow:])


def _runs(by_hour: tuple[float | None, ...], starts: list[int], hours: int) -> list[int]:
    """The slots among `starts` from which `hours` slots in a row all have a price."""
    # RULE: a run of hours uses only hours that all have a price
    return [o for o in starts if None not in by_hour[o : o + hours] and o + hours <= len(by_hour)]


def _run_of(hours: int) -> str:
    return "hour" if hours == 1 else f"{hours} hours in a row"


def _run_said(run: dict[str, Any], cost: str, *, not_out: bool) -> str:
    """The sentence for a run: when it starts and ends, what it costs, and why it ends by midnight."""
    starts, ends = ("starts", "ends") if run["hours"] == 1 else ("start", "end")
    late = " Tomorrow's prices aren't out yet, so this ends by midnight." if not_out else ""
    return (
        f"The cheapest {_run_of(run['hours'])} {starts} at {run['start']} and {ends} at {run['end']}, "
        f"at {cost}.{late}"
    )


def _unreachable(error: ReaderError) -> str:
    """What to say when the reader was not read: that its name is not known, or that it did not answer."""
    # RULE: a reader whose name is not found is said as a name, not its power
    return NOT_FOUND if isinstance(error, NameNotFoundError) else UNREACHABLE


def _age(seconds: float) -> str:
    minutes = 120
    if seconds < minutes:
        return f"{int(seconds)} seconds"
    return f"{int(seconds // 60)} minutes"
