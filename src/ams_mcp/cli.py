# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for ams-mcp: serve the meter's tools to a voice session, or ask one question."""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from ams_mcp.reader import HttpReader
from ams_mcp.server import build_server
from ams_mcp.service import PERIODS, AmsService
from ams_mcp.settings import SettingsError, load_settings, local_timezone, settings_path
from ams_mcp.simulator import SimulatedReader

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "ask")
#: What `ask` can ask: one per tool.
QUESTIONS = ("now", "energy", "price", "capacity", "history")


def _service(*, simulate: bool, settings_file: str | None) -> AmsService:
    if simulate:
        return AmsService(SimulatedReader(), timezone=local_timezone())
    path = pathlib.Path(settings_file).expanduser() if settings_file else settings_path()
    settings = load_settings(path)
    return AmsService(HttpReader(settings.address), timezone=settings.timezone, where=settings.address)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="ams-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("question", nargs="?", choices=QUESTIONS, help="for ask: which tool's answer")
    parser.add_argument("--period", choices=PERIODS, default="day", help="for ask history")
    parser.add_argument("--simulate", action="store_true", help="a pretend meter reader, no network")
    parser.add_argument("--settings-file", help="instead of ~/.config/ams-mcp/config.toml")
    args = parser.parse_args(argv)
    try:
        service = _service(simulate=args.simulate, settings_file=args.settings_file)
    except SettingsError as error:
        sys.stderr.write(f"ams-mcp: {error}\n")
        return 2
    if args.command == "serve":  # pragma: no cover - runs until stdin closes
        asyncio.run(build_server(service, simulated=args.simulate).run_stdio_async())
        return 0
    question = args.question or "now"
    if question == "history":
        answer = asyncio.run(service.history(args.period))
    else:
        answer = asyncio.run(getattr(service, question)())
    prefix = "Simulated: " if args.simulate else ""
    sys.stdout.write(prefix + answer["say"] + "\n")
    return 0 if answer["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
