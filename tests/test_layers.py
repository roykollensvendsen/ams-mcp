# SPDX-License-Identifier: GPL-3.0-or-later
"""The layers: the core, then the reader, then the service, then the server and the command line.

The core knows neither the network nor MCP. Only the reader layer talks to
the meter reader, so every request passes its one allow-list of pages. The
service turns pages into answers without opening a connection. Only the top
puts the answers behind MCP and the command line. Every import counts,
including those made only for type checking.

The map of the layers is in `scripts/ratchet.py`, which also counts the
breaches for the code-health page; here any breach at all is refused.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "ams_mcp"


def _ratchet():
    spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchet = _ratchet()


def _package(tmp_path, **modules):
    package = tmp_path / "ams_mcp"
    package.mkdir()
    for name in ("__init__", "pages", "reader", "service", "server"):
        (package / f"{name}.py").write_text(modules.get(name, ""))
    return package


def test_every_module_has_a_layer_and_imports_only_what_its_layer_may():
    assert ratchet.layer_breaches(PACKAGE) == []


def test_every_module_in_the_package_is_on_the_map():
    modules = {p.stem for p in PACKAGE.glob("*.py")}
    assert {ratchet.layer(m) for m in modules} <= set(ratchet.ALLOWED)


@pytest.mark.parametrize(
    ("module", "source", "breach"),
    [
        (
            "service",
            "from ams_mcp.server import build_server\n",
            "service (service) imports ams_mcp.server (top)",
        ),
        ("pages", "from ams_mcp import reader\n", "pages (core) imports ams_mcp.reader (reader)"),
        ("pages", "import httpx\n", "pages (core) imports httpx"),
        ("service", "import httpx\n", "service (service) imports httpx"),
        ("server", "import httpx\n", "server (top) imports httpx"),
        ("reader", "from mcp.types import ToolAnnotations\n", "reader (reader) imports mcp.types"),
    ],
)
def test_an_import_a_layer_may_not_use_is_a_breach(tmp_path, module, source, breach):
    breaches = ratchet.layer_breaches(_package(tmp_path, **{module: source}))
    assert breach in breaches
    assert all(b.startswith(f"{module} ") for b in breaches)


def test_an_import_made_only_for_type_checking_still_counts(tmp_path):
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from ams_mcp.server import x\n"
    assert ratchet.layer_breaches(_package(tmp_path, service=source)) == [
        "service (service) imports ams_mcp.server (top)"
    ]


def test_a_module_with_no_layer_is_a_breach(tmp_path):
    package = _package(tmp_path)
    (package / "stray.py").write_text("")
    assert ratchet.layer_breaches(package) == ["stray has no layer"]


def test_the_code_health_page_gets_the_count():
    assert "layer_violations" in ratchet.LOWER
