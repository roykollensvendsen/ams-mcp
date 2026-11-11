# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP server: the meter's five read-only tools, over stdio for a voice session.

Run with ``ams-mcp serve``; ``ams-mcp serve --simulate`` answers the same tools
from a pretend reader (ams_mcp.simulator), and every answer then says
"Simulated:" first.
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ams_mcp.service import AmsService

INSTRUCTIONS = (
    "Reads the home's electricity meter through its HAN-port reader: power now, energy used, the "
    "electricity price, the capacity step and a short history. Read each 'say' aloud as it is; it "
    "is in kW, kWh and kr. When it starts 'Not confirmed', say so. Every tool only reads: none "
    "changes the meter or the reader."
)
SIMULATED = "A SIMULATED meter reader, not the real one: say so. "
#: The Claude app gives up on a call after about 15 seconds; every tool answers before then.
ANSWER_WITHIN = 12.0
#: What the voice hears when a tool is still working by then.
WORKING = "The meter reader is slow to answer just now. Ask again in a moment."
#: What the voice hears when ams-mcp itself fails, so it never hears only that the tool failed.
BROKE = "That went wrong inside ams-mcp. Try again, and if it fails again, check its log."

READ_ONLY = ToolAnnotations(read_only_hint=True)


class _Guarded:
    """Registers each tool so a failure or a slow reader is still an answer the voice can read."""

    def __init__(self, server: MCPServer[Any], *, simulated: bool) -> None:
        self._server, self._simulated = server, simulated

    def tool(self, **options: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:  # noqa: ANN401
        register = self._server.tool(annotations=READ_ONLY, **options)

        def guard(tool: Callable[..., Awaitable[dict[str, Any]]]) -> Any:  # noqa: ANN401
            @functools.wraps(tool)
            async def answered(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
                try:
                    # RULE: a tool still working at the deadline says so
                    answer = await asyncio.wait_for(tool(*args, **kwargs), ANSWER_WITHIN)
                except TimeoutError:
                    answer = {"success": False, "say": WORKING}
                except Exception:  # noqa: BLE001 - whatever broke, the voice gets a sentence
                    # RULE: every tool answers with a sentence, even when it fails
                    answer = {"success": False, "say": BROKE}
                answer["simulated"] = self._simulated
                # RULE: every simulated answer says simulated first
                if self._simulated:
                    answer["say"] = "Simulated: " + answer["say"]
                return answer

            return register(answered)

        return guard


def build_server(service: AmsService, *, simulated: bool = False) -> MCPServer[Any]:
    """The MCP server with the meter's tools, all of them read-only."""
    server: MCPServer[Any] = MCPServer(
        name="ams-simulated" if simulated else "ams",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )
    guarded = _Guarded(server, simulated=simulated)

    @guarded.tool(
        description=(
            "Power used right now.\nkW drawn from or sent to the grid, each phase's volts and amps, "
            "and the power factor when the meter sends one."
        )
    )
    async def ams_now() -> dict[str, Any]:
        return await service.now()

    @guarded.tool(
        description=(
            "Energy used so far.\nkWh in and out, and what it cost, this hour, today and this month."
        )
    )
    async def ams_energy() -> dict[str, Any]:
        return await service.energy()

    @guarded.tool(
        description=(
            "Electricity price by hour.\nThe price now in kr per kWh, the next hours, and the cheapest "
            "hours left today."
        )
    )
    async def ams_price() -> dict[str, Any]:
        return await service.price()

    @guarded.tool(
        description=(
            "Capacity step this month.\nThe grid's capacity step, the month's top three hours, and how "
            "much this hour could use before the step goes up."
        )
    )
    async def ams_capacity() -> dict[str, Any]:
        return await service.capacity()

    @guarded.tool(
        description=(
            "A day or month in short.\nPeriod 'day' (the default): today so far and its busiest hour. "
            "'month': this month so far, its busiest day, and last month."
        )
    )
    async def ams_history(period: Literal["day", "month"] = "day") -> dict[str, Any]:
        return await service.history(period)

    return server
