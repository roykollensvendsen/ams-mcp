# The reader's pages, key by key

AmsToMqttBridge names its JSON fields with one or two letters. This page says
what each one this plugin reads means, as the firmware's own source writes it.

*Read from [UtilitechAS/amsreader-firmware](https://github.com/UtilitechAS/amsreader-firmware)
at tag `v2.4.11` (commit `1ed701fc570e9d916876feaeb98b8b90da052696`), in
`lib/SvelteUi/json/*.json` and `lib/SvelteUi/src/AmsWebServer.cpp`, on
2026-11-11. The version is pinned in `ams_mcp.pages.FIRMWARE`, and
`tests/test_recorded_pages.py` checks the recordings came from it.*

All seven pages answer GET without a login when the reader's security setting
is off. Each is read-only in that version.

## data.json: the latest reading

| Key | Means | Unit |
|---|---|---|
| `i`, `e` | active power in from, and out to, the grid | W |
| `w` | `i` minus `e` | W |
| `ri`, `re` | reactive power in and out | var |
| `ic`, `ec` | the meter's total active energy in and out | kWh |
| `f` | power factor; 0 when the meter sends none | |
| `l1`, `l2`, `l3` | each phase: `u` volts, `i` amps, `p`/`q` active power in/out (W, 0 when not sent), `f` power factor | |
| `l2.e` | true when the meter leaves out L2's current | |
| `p`, `px` | the price now, in and out; `null` when there is none | currency per kWh |
| `pe` | whether there is a price now | |
| `pc`, `pr` | the price's currency and region | |
| `ea.x` | the month's capacity figure: the average of its top hours | kWh in an hour |
| `ea.p` | the month's top hours, one per day, largest first | kWh |
| `ea.t` | the upper edge of the capacity step the month is in | kW |
| `ea.h`, `ea.d`, `ea.m` | this hour, today, this month: `u` used, `c` its cost, `p` produced, `i` its income | kWh, currency |
| `hm` | the reader's view of the meter: 1 read under 15 s ago, 2 under 30 s, 3 older or failing, 0 not yet | |
| `c` | the reader's clock | Unix seconds |
| `im`, `om`, `mf` | the configured largest import, production and main fuse | W, W, A |
| `v`, `r`, `t`, `u`, `m` | the reader's own supply voltage, Wi-Fi signal, temperature, uptime and free memory | |
| `em`, `wm`, `mm`, `me`, `he`, `ee`, `ds`, `mt`, `a` | the reader's own status codes | |

`hm` is what "Not confirmed" rests on: anything but 1 or 2 means the reader has
not heard from the meter for about 30 seconds.

## tariff.json: the capacity steps

| Key | Means |
|---|---|
| `t` | ten step edges in kW, from the reader's settings; 65535 marks no edge |
| `p` | the month's top hours: `d` day of the month, `v` kWh |
| `c` | the edge of the step the month is in now, kW |
| `m` | the average of the top hours, kWh in an hour |

The firmware counts the month in the first step whose edge is at or above `m`.
One hour per day can be among the top hours, so an hour today can only replace
today's own entry.

## energyprice.json: prices from now

`currency` and `source`, then `"00"` to `"35"`: the price for this hour and the
35 after it, counted from the moment the page is read, with the reader's own
additions applied. `null` where there is none.

## dayplot.json and monthplot.json: hour by hour, day by day

`dayplot.json` holds `i00` to `i23` (in) and `e00` to `e23` (out), in kWh, each
in the slot of the **UTC** hour it began. The 24 slots are the last 24 full
hours; the slot of the hour now running still holds the same hour yesterday.

`monthplot.json` holds `i01` to `i31` and `e01` to `e31`, in kWh, by **local**
day of the month. The days before today are this month's; today and the days
after still hold last month's.

## temperature.json and sysinfo.json

`temperature.json`: `c` the number of sensors, `s` each one's `i` index, `a`
hardware address (never kept), and `v` degrees Celsius.

`sysinfo.json` carries the firmware `version` and `last_month` (`u`, `c`, `p`,
`i`, as in `ea.m`). Everything else on it, such as the meter's id, the network
addresses and the host name, is never read into an answer, and is replaced in
the recording.
