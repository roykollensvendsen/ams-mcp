<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# AGENTS.md

Instructions for coding agents working in this repository. People should
start with [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md);
this file is the same rules, for an agent.

## What this is

Reads a home electricity meter for voice: an MCP server that claude-voice can
take in as an optional plugin. It asks the meter's HAN-port reader
(AmsToMqttBridge) for its JSON pages and answers in a sentence. Why it reads
over HTTP, and only reads, is [ADR-AM-001](decisions/ADR-AM-001-read-the-readers-own-pages-over-http.md).

## Hard rules

1. **Only GET, only the seven pages in `reader.PAGES`.** Never POST, never a
   configuration, upgrade, reboot, Wi-Fi or MQTT page, never a login. The
   allow-list has a test and a mutation row; never widen it to make a call work.
2. **Nothing about the owner's home is committed**: no home-network address,
   hardware address, host name, meter id or serial, Wi-Fi name, key or token.
   Examples use 192.0.2.x and 00:00:5E:00:53:xx. A page recorded off the reader
   has those replaced before it is committed, and `tests/test_recorded_pages.py`
   checks it. `scripts/private_data_scan.py` refuses the rest.
3. **No link to a Claude session in a commit message.** No `Claude-Session:`
   line, whatever an assistant's own attribution says;
   `scripts/no_session_links.py` refuses it.
4. **The simulator first.** Every 'say' it gives starts "Simulated:".
5. Every line marked `# RULE:` has a row in `scripts/mutations.toml` and a
   test named after it, and `uv run python scripts/mutate.py` must show each
   rule's own test going red.

## Commands

The gates are in [CONTRIBUTING.md](CONTRIBUTING.md#the-gates); run them all
after the last edit.
