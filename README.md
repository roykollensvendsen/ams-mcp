# ams-mcp

Reads a home electricity meter by voice. It is a small MCP server that asks the
meter's HAN-port reader, an [AmsToMqttBridge](https://github.com/UtilitechAS/amsreader-firmware)
device on the home network, what it has seen, and answers in a sentence to read
aloud. It is made as a plugin for claude-voice, and works with any MCP client
over stdio.

It only reads. It sends nothing but GET, to seven JSON pages the reader shows
without a login, and never a page that saves a setting, reboots or upgrades
([ADR-AM-001](decisions/ADR-AM-001-read-the-readers-own-pages-over-http.md)).

## The five tools

| Tool | Says |
|---|---|
| `ams_now` | the power drawn or sent now, each phase's volts and amps, and the power factor |
| `ams_energy` | kWh in and out, and what it cost, this hour, today and this month |
| `ams_price` | the price now, the next hours, and the cheapest hours left today |
| `ams_capacity` | the capacity step, the month's top three hours, and how far this hour is from the next step |
| `ams_history` | today by hour, or this month by day, in short |

Every answer has `say`, in kW, kWh and kr, `success`, and `read_at`, the time
of the reading. A reading older than about 30 seconds, or one the reader has
not had from the meter lately, starts "Not confirmed".

## Try it without the meter

`--simulate` answers from pages recorded off a real reader, and says so:

```console
$ ams-mcp ask now --simulate
Simulated: Drawing 1.05 kW from the grid now. The phases carry 2.9, 0.9 and 1.5 A. This meter sends no power factor.
$ ams-mcp ask energy --simulate
Simulated: Today 33.2 kWh, 30.85 kr. This hour 0.32 kWh. This month 605 kWh, 548 kr.
```

## Point it at the reader

The address goes in a file of your own, never in this repository:

<!-- not run: it writes into the reader's own settings folder -->
```toml
# ~/.config/ams-mcp/config.toml
[reader]
address = "192.0.2.10"     # the reader's address on your network
timezone = "Europe/Oslo"   # optional; the computer's own by default
```

Without it, a question says where the file should be:

```console
$ ams-mcp ask now --settings-file /nowhere/config.toml
ams-mcp: There is no settings file at /nowhere/config.toml. Write [reader] address = "..." there, or use --simulate.
```

`ams-mcp serve` still starts without it, so the voice sees the plugin, and
every tool answers with the same advice and "Then restart the plugin." It
says so once on its error output, and serves until its input closes:

```console
$ ams-mcp serve --settings-file /nowhere/config.toml < /dev/null
ams-mcp: There is no settings file at /nowhere/config.toml. Write [reader] address = "..." there, or use --simulate.
```

`ams-mcp serve` then serves the tools over stdio. For claude-voice:

<!-- not run: it is a block for claude-voice's own settings file -->
```toml
[plugins.ams]
command = "uv"
args = ["run", "ams-mcp", "serve"]
cwd = "~/ams-mcp"
tools = ["ams_now", "ams_energy", "ams_price", "ams_capacity", "ams_history"]
timeout = 30
```

What each of the reader's short keys means, read from the firmware's source,
is in [docs/pages.md](docs/pages.md).

## The gates

Every one of these runs in continuous integration, and all of them run here:

```console
$ uv run ruff check .
All checks passed!
```

<!-- not run: the workflow runs each of these; nesting them here would run the suite inside itself -->
```bash
uv run ruff format --check .   # layout
uv run mypy src                # types
uv run pytest -q               # behaviour
python scripts/mutate.py       # that each rule's own test can fail
```

`CONTRIBUTING.md` says what each one is for, and
[`decisions/`](decisions/README.md) records the choices that shaped them,
including how a change is made here.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
