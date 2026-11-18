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

The pretend reader is read through the same code as the real one, and keeps
its limits: one visitor at a time, about a twentieth of a second a page. It can
also be put in a state the real reader has been seen in, to hear what the voice
then says. `asleep` makes the first answer wait six seconds while it wakes its
Wi-Fi, `off` is not found on the network, `meter-quiet` has had no reading
from the meter lately, and `login` asks for one:

```console
$ ams-mcp ask now --simulate meter-quiet
Simulated: Not confirmed: the reader has not heard from the meter for over 30 seconds. Drawing 1.05 kW from the grid now. The phases carry 2.9, 0.9 and 1.5 A. This meter sends no power factor.
```

## Set it up

It needs an [AmsToMqttBridge](https://github.com/UtilitechAS/amsreader-firmware)
reader on the home network, and [uv](https://docs.astral.sh/uv/). The setup guide,
[docs/setup.md](docs/setup.md), is written so an agent can follow it alone. Every
step there has a check. Some steps only the owner can do, and they are listed in
full here.

<!-- steps:begin -->
<!-- Made by scripts/docs.py from docs/setup.md. Change the guide, then run it. -->

The steps, in full, are in [docs/setup.md](docs/setup.md):

1. Install the project
2. Hear the plugin read a recorded reader
3. Write the reader's address into the plugin's settings file
4. Add the plugin to claude-voice

### Steps only the owner can do

#### A. Find the reader's name or address in the router

Why: the router's list of devices is behind the owner's own login, and the address
belongs to the owner's home network.

Look in the router's list of devices for the meter reader. Its name there, such as
`amsreader.invalid`, is the better choice. Use its IP address if the router shows no
name.

Check: `curl -s http://<the name or address>/data.json` prints a short JSON page.

#### B. Approve the plugin in claude-voice

Why: claude-voice offers a new plugin's tools only after the owner types its approval.

<!-- not run: it asks for typed input -->
```bash
claude-voice plugins approve ams
```

Check: `claude-voice plugins list` shows `ams` as `approved`.

<!-- steps:end -->

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
