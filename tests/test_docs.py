# SPDX-License-Identifier: GPL-3.0-or-later
"""The README's step list is what the setup guide says, and the guide is one an agent can follow.

Copied from messages-mcp (GPL-3.0-or-later), new-plugin-v8's "Docs: the same three parts".
"""

import os
import pathlib
import sys

import pytest
from bounded import run_bounded

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import docs  # noqa: E402

SETUP = ROOT / "docs/setup.md"
GUIDE = """# A plugin

## Steps

### 1. Install it

<!-- safe-to-run -->
```
echo installed
```

Check: `echo ok` prints ok.

### 2. Start it

```
systemctl --user start it
```

Check: `systemctl --user is-active it` prints active.

## Steps only the owner can do

### A. Log in

<!-- owner -->
Why: it is a login.

Check: the app says logged in.
"""


def _steps():
    return docs.steps(SETUP.read_text(encoding="utf-8"))


def test_the_readmes_step_list_is_what_the_setup_guide_makes_now():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert docs.updated(readme, SETUP.read_text(encoding="utf-8")) == readme, (
        "README.md's step list is stale: run `uv run scripts/docs.py`"
    )


def test_every_step_in_the_setup_guide_has_a_check():
    assert _steps(), "docs/setup.md has no steps"
    assert [s.number for s in _steps() if not s.check] == []


def test_every_agent_step_shows_its_commands():
    assert [s.number for s in _steps() if not s.owner and not s.blocks] == []


def test_the_setup_guide_has_the_owner_heading_with_a_step_or_none():
    text = SETUP.read_text(encoding="utf-8")
    assert "\n## Steps only the owner can do\n" in text
    owner_part = text.split("\n## Steps only the owner can do\n", 1)[1].split("\n## ", 1)[0]
    assert any(s.owner for s in _steps()) or owner_part.strip() == "None."


def test_every_step_under_the_owner_heading_is_marked_and_says_why():
    under = [s for s in _steps() if s.in_owner_section]
    assert under, "the owner heading holds no step"
    assert [s.number for s in under if not s.owner] == [], "mark each with <!-- owner -->"
    assert [s.number for s in under if not s.why] == [], "say why only the owner can do it"
    assert [s.number for s in _steps() if s.owner and not s.in_owner_section] == []


def test_the_readme_lists_each_agent_step_by_its_first_line_and_each_owner_step_in_full():
    made = docs.block(GUIDE)
    assert "1. Install it\n2. Start it\n" in made
    assert "systemctl --user start it" not in made
    assert "#### A. Log in\n\nWhy: it is a login.\n\nCheck: the app says logged in.\n" in made
    assert "<!-- owner -->" not in made
    assert docs.block(GUIDE.split("### A.", maxsplit=1)[0]).count("None.") == 1


def test_only_a_block_marked_safe_to_run_is_ever_run():
    blocks = [(s.number, b.safe) for s in docs.steps(GUIDE) for b in s.blocks]
    assert blocks == [("1", True), ("2", False)]


def test_a_stale_readme_fails_the_check_and_says_how_to_fix_it(tmp_path, monkeypatch, capsys):
    (tmp_path / "setup.md").write_text(GUIDE)
    readme = tmp_path / "README.md"
    readme.write_text("# A plugin\n\n<!-- steps:begin -->\nold\n<!-- steps:end -->\n")
    monkeypatch.setattr(docs, "SETUP", tmp_path / "setup.md")
    monkeypatch.setattr(docs, "README", readme)
    assert docs.main(["--check"]) == 1
    assert "uv run scripts/docs.py" in capsys.readouterr().err
    assert "old" in readme.read_text(), "--check changes nothing"
    assert docs.main([]) == 0
    assert docs.main(["--check"]) == 0


#: The only variables a safe step is given from the test's own environment. Anything else,
#: such as a token or a key, never reaches a command the guide shows.
PASSED_ON = ("PATH", "LANG", "LC_ALL", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")


def _a_home_of_its_own(tmp_path):
    """The environment a safe step runs in: every folder it could write lies in `tmp_path`."""
    env = {k: os.environ[k] for k in PASSED_ON if k in os.environ}
    env["HOME"] = str(tmp_path / "home")
    pathlib.Path(env["HOME"]).mkdir()
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"):
        env[name] = str(tmp_path / name.lower())
        pathlib.Path(env[name]).mkdir()
    # uv's cache stays where it was, so a safe step needs no network.
    env.setdefault("UV_CACHE_DIR", os.environ.get("UV_CACHE_DIR", str(pathlib.Path.home() / ".cache/uv")))
    return env


def test_a_safe_step_runs_with_home_and_its_folders_in_the_tests_own_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "made-up-token")
    monkeypatch.setenv("SOME_API_KEY", "made-up-key")
    env = _a_home_of_its_own(tmp_path)
    moved = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"}
    assert set(env) <= {*PASSED_ON, *moved}, "a safe step is given only the variables it needs"
    assert "GH_TOKEN" not in env
    assert "SOME_API_KEY" not in env
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR"):
        assert pathlib.Path(env[name]).is_relative_to(tmp_path), name


SAFE = [(s.number, b.code) for s in docs.steps(SETUP.read_text(encoding="utf-8")) for b in s.blocks if b.safe]


@pytest.mark.parametrize(("number", "code"), SAFE, ids=[n for n, _ in SAFE])
def test_each_step_marked_safe_to_run_runs(tmp_path, number, code):
    done = run_bounded(
        ["bash", "-euo", "pipefail", "-c", code],
        cwd=ROOT,
        env=_a_home_of_its_own(tmp_path),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, f"step {number} failed:\n{done.stdout}\n{done.stderr}"
