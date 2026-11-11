# SPDX-License-Identifier: GPL-3.0-or-later
"""No two tool names the voice could mix up (the plugin standard's rule 1).

Names that look or sound alike are listed here on purpose; each pair opens its
description with different words, which is what the voice chooses by.
"""

import asyncio
import difflib
import zoneinfo

from ams_mcp.server import build_server
from ams_mcp.service import AmsService
from ams_mcp.simulator import SimulatedReader

#: Pairs that look or sound alike. None today; a new tool that comes close is listed here.
CONFUSABLE: set[frozenset[str]] = set()
#: How alike two names may be, as difflib reckons it, before the pair must be listed.
ALIKE = 0.75


def _tools():
    service = AmsService(SimulatedReader(), timezone=zoneinfo.ZoneInfo("Europe/Oslo"))
    return asyncio.run(build_server(service).list_tools())


def test_no_two_tool_names_can_be_confused():
    tools = _tools()
    for i, one in enumerate(tools):
        for other in tools[i + 1 :]:
            alike = difflib.SequenceMatcher(None, one.name, other.name).ratio()
            if alike >= ALIKE:
                assert frozenset({one.name, other.name}) in CONFUSABLE, (one.name, other.name, alike)


def test_no_tool_asks_for_a_confirmation_because_none_changes_anything():
    # Nothing here is risky (rule 7): every tool only reads. A tool that would
    # change the reader is outside this plugin altogether (decisions/ADR-AM-001).
    assert not [t.name for t in _tools() if "request" in t.name or "confirm" in t.name]
