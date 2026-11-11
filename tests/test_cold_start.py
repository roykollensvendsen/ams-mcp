# SPDX-License-Identifier: GPL-3.0-or-later
"""A freshly started server lists its tools quickly, and never needs the reader to do it.

The bridge starts each plugin as a process of its own and asks it for its
tools. A plugin that does not answer in time is not offered. So listing the
tools must not wait on the meter reader or the network: a reader that is
switched off must still leave the voice a plugin that says so. Both runs here
start the real command, as the bridge does, from a process that has never
imported the package.
"""

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: Seconds a cold start may take to list the tools, kept well inside the bridge's 15.
COLD_START_WITHIN = 10.0
TOOLS = {"ams_now", "ams_energy", "ams_price", "ams_capacity", "ams_history"}


def _listed(args, env=None):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "ams_mcp.cli", *args], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}

    started = time.monotonic()
    names = asyncio.run(run())
    return names, time.monotonic() - started


def test_a_cold_start_lists_the_tools_in_time():
    names, seconds = _listed(["serve", "--simulate"])
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN


def test_a_cold_start_never_waits_on_the_reader(tmp_path):
    # 192.0.2.10 is set aside for documentation: nothing answers there.
    settings = tmp_path / "config.toml"
    settings.write_text('[reader]\naddress = "192.0.2.10"\n')
    env = {**os.environ, "HOME": str(tmp_path)}
    names, seconds = _listed(["serve", "--settings-file", str(settings)], env=env)
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN


def test_a_plugin_without_settings_still_starts_and_says_what_to_write(tmp_path):
    # Without a settings file the bridge used to see a process that exited at
    # once, and the voice saw no plugin at all, so nothing could say what was wrong.
    missing = tmp_path / "config.toml"

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "ams_mcp.cli", "serve", "--settings-file", str(missing)]
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            answer = (await session.call_tool("ams_now", {})).structured_content
            return names, answer

    names, answer = asyncio.run(run())
    assert names == TOOLS
    assert answer["success"] is False
    assert str(missing) in answer["say"]
    assert '[reader] address = "..."' in answer["say"]
    assert answer["say"].endswith("Then restart the plugin.")
