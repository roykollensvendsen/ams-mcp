# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's settings: where the meter reader is, kept outside the repository.

    # ~/.config/ams-mcp/config.toml
    [reader]
    address = "192.0.2.10"          # the reader's address on the home network
    timezone = "Europe/Oslo"        # optional; the computer's own by default

There is no login: the reader's JSON pages need none, and this plugin never
sends one.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import tomllib
import zoneinfo
from dataclasses import dataclass

#: A host name or an IPv4 address, nothing more: no scheme, port or path.
_ADDRESS = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


class SettingsError(ValueError):
    """The settings file is missing, or says something this plugin cannot use."""


@dataclass(frozen=True)
class Settings:
    """Where the reader is, and the time zone its days and hours are counted in."""

    address: str
    timezone: dt.tzinfo


def settings_path() -> pathlib.Path:
    """The settings file's usual place."""
    return pathlib.Path("~/.config/ams-mcp/config.toml").expanduser()


def local_timezone() -> dt.tzinfo:
    """The computer's own time zone."""
    zone = dt.datetime.now().astimezone().tzinfo
    return zone or dt.UTC


def load_settings(path: pathlib.Path) -> Settings:
    """The settings in the file, checked."""
    if not path.is_file():
        msg = f'There is no settings file at {path}. Write [reader] address = "..." there, or use --simulate.'
        raise SettingsError(msg)
    try:
        table = tomllib.loads(path.read_text()).get("reader", {})
    except tomllib.TOMLDecodeError as error:
        msg = f"{path} is not valid TOML: {error}"
        raise SettingsError(msg) from error
    address = table.get("address")
    # RULE: the address is a bare host, so no other page can be reached through it
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
        msg = f"[reader] address in {path} must be a bare host name or IP address, no scheme, port or path."
        raise SettingsError(msg)
    zone_name = table.get("timezone")
    try:
        zone = zoneinfo.ZoneInfo(zone_name) if zone_name else local_timezone()
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
        msg = f"[reader] timezone {zone_name!r} in {path} is not a known time zone."
        raise SettingsError(msg) from error
    return Settings(address, zone)
