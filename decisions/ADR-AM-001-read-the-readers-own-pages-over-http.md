# ADR-AM-001: Read the reader's own pages over HTTP, with GET only

## Status

Accepted 2026-11-11 by the owner, through claude-voice's manager: version 1
reads over HTTP and only reads.

## Context

The meter's HAN port is read by an AmsToMqttBridge v2.4.11 device on the home
network. It shows what it reads on JSON pages that answer GET without a login,
and it can also publish to an MQTT broker, which the home does not run yet.

The same web server takes POST for saving settings, rebooting, factory reset,
uploading firmware and certificates, and editing the stored day and month
figures, and shows a configuration page that holds the Wi-Fi and MQTT
passwords. A voice plugin has no reason to reach any of them.

## Options considered

- **MQTT from a home broker.** The reader pushes each reading, so nothing polls.
  Not taken first: it needs a broker installed and the reader's settings
  changed, both outside this work.
- **Reading the meter's serial port directly.** The reader already does that
  well, and the port has one listener.
- **The reader's JSON pages over HTTP.** Needs no change on the device or the
  network, and gives everything the five tools need. Taken.
- **Doing nothing.** The owner would keep opening the reader's web page.

## Decision

ams-mcp reads seven pages, `data.json`, `dayplot.json`, `monthplot.json`,
`energyprice.json`, `tariff.json`, `temperature.json` and `sysinfo.json`, with
GET, only when a tool is asked, and keeps each for three seconds. No other
path is reachable: an allow-list refuses anything else before a request is
made, the address in the settings must be a bare host, redirects are not
followed, and no login is ever sent. Each of those is a rule with a test and a
row in `scripts/mutations.toml`.

## Consequences

- Nothing on the reader can be changed through this plugin, whatever a voice
  session asks for.
- A reading is as old as the reader's own; the plugin says "Not confirmed" when
  the reader has not heard from the meter for about 30 seconds.
- The field map is tied to one firmware version. A reader upgraded to another
  may change a key, and then the plugin refuses rather than guesses.
- Polling misses what happens between questions. MQTT would not; it is in
  [deferred.md](deferred.md) with its trigger.

## Related

- [docs/pages.md](../docs/pages.md), the key map.
- `src/ams_mcp/reader.py`, the allow-list.
