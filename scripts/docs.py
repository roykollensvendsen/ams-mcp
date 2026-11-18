#!/usr/bin/env python3
"""Make the README's list of steps from the setup guide, so the two never say different things.

``docs/setup.md`` is the one source. It is written for an agent, top to bottom:

- each step is a heading ``### <number>. <what it does>``, with a fenced command block and a
  line starting ``Check:`` that names the command whose output proves the step worked;
- a block runs in the tests only when the line above its fence is ``<!-- safe-to-run -->``;
- the steps under ``## Steps only the owner can do`` are marked ``<!-- owner -->``, and each
  says ``Why:`` only the owner can do it.

The tests run only the blocks marked safe. A ``Check:`` line is never run, since it reads the
state of the owner's machine, which a test does not have; the tests only make sure it is there.

The README, for people, holds between ``<!-- steps:begin -->`` and ``<!-- steps:end -->`` the
first line of each agent step and every owner step in full. This script writes that block:

    uv run scripts/docs.py           # write the README's block from the guide
    uv run scripts/docs.py --check   # 1, and what to run, when the block is stale
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "docs/setup.md"
README = ROOT / "README.md"
BEGIN, END = "<!-- steps:begin -->", "<!-- steps:end -->"
OWNER_HEADING = "## Steps only the owner can do"
OWNER, SAFE = "<!-- owner -->", "<!-- safe-to-run -->"
STEP = re.compile(r"^### (\w+)\. (.+?)\s*$")
FIX = "run `uv run scripts/docs.py` and commit the README"


@dataclasses.dataclass(frozen=True)
class Block:
    """One fenced command block in a step."""

    code: str
    safe: bool


@dataclasses.dataclass(frozen=True)
class Step:
    """One step of the guide, as it is written."""

    number: str
    title: str
    lines: tuple[str, ...]
    in_owner_section: bool

    @property
    def owner(self) -> bool:
        """Marked as a step only the owner can do."""
        return OWNER in (line.strip() for line in self.lines)

    @property
    def check(self) -> str:
        """What follows ``Check:``, or nothing when the step has no check."""
        for line in self.lines:
            if line.startswith("Check:"):
                return line.removeprefix("Check:").strip()
        return ""

    @property
    def why(self) -> str:
        """What follows ``Why:``, the reason only the owner can do it."""
        for line in self.lines:
            if line.startswith("Why:"):
                return line.removeprefix("Why:").strip()
        return ""

    @property
    def blocks(self) -> list[Block]:
        """The step's fenced blocks, each marked safe to run or not."""
        found, inside, code, safe = [], False, [], False
        for i, line in enumerate(self.lines):
            if line.startswith("```") and not inside:
                # RULE: only a block marked safe to run is ever run
                inside, code, safe = True, [], i > 0 and self.lines[i - 1].strip() == SAFE
            elif line.startswith("```"):
                inside = False
                found.append(Block("\n".join(code), safe))
            elif inside:
                code.append(line)
        return found


def steps(guide: str) -> list[Step]:
    """Every step in the guide, in order."""
    found: list[Step] = []
    number = title = None
    body: list[str] = []
    owner_section = False

    def close() -> None:
        if number is not None and title is not None:
            found.append(Step(number, title, tuple(_trimmed(body)), owner_section))

    for line in guide.splitlines():
        heading = STEP.match(line)
        if heading or line.startswith(("## ", "# ")):
            close()
            number = title = None
            body = []
        if line.strip() == OWNER_HEADING:
            owner_section = True
        elif line.startswith("## "):
            owner_section = False
        if heading:
            number, title = heading.groups()
        elif number is not None:
            body.append(line)
    close()
    return found


def _trimmed(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def block(guide: str) -> str:
    """The README's step list, as made from the guide now, markers included."""
    agent = [s for s in steps(guide) if not s.owner]
    owner = [s for s in steps(guide) if s.owner]
    out = [
        BEGIN,
        "<!-- Made by scripts/docs.py from docs/setup.md. Change the guide, then run it. -->",
        "",
        "The steps, in full, are in [docs/setup.md](docs/setup.md):",
        "",
    ]
    out += [f"{s.number}. {s.title}" for s in agent]
    out += ["", "### Steps only the owner can do", ""]
    if not owner:
        out += ["None.", ""]
    for s in owner:
        shown = [line for line in s.lines if line.strip() not in (OWNER, SAFE)]
        out += [f"#### {s.number}. {s.title}", "", *shown, ""]
    out.append(END)
    return "\n".join(out)


def updated(readme: str, guide: str) -> str:
    """The README with its block replaced by the one the guide makes now."""
    if BEGIN not in readme or END not in readme:
        msg = f"README.md has no {BEGIN} … {END} block; add the two markers, then {FIX}"
        raise SystemExit(msg)
    head, rest = readme.split(BEGIN, 1)
    tail = rest.split(END, 1)[1]
    return head + block(guide) + tail


def main(argv: list[str] | None = None) -> int:
    """Write the README's block, or with --check say whether it is stale."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="change nothing; 1 when the README is stale")
    args = parser.parse_args(argv)
    readme = README.read_text(encoding="utf-8")
    fresh = updated(readme, SETUP.read_text(encoding="utf-8"))
    # RULE: a stale readme fails the check and says how to fix it
    if args.check and fresh != readme:
        sys.stderr.write(f"README.md's step list is not what docs/setup.md says now; {FIX}.\n")
        return 1
    if not args.check and fresh != readme:
        README.write_text(fresh, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
