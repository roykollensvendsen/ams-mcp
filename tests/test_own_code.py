"""The check tools measure this checkout's own code, never another checkout's installed copy.

Two worktrees that share one Python environment install the package from whichever ran
`uv sync` last. A mutation run in one then switches a rule off there while the tests load
the other's code, and every rule looks unguarded. So `scripts/mutate.py` and
`scripts/ratchet.py` ask the suite's own interpreter, in a process of its own, where the
package comes from, and refuse to go on when it is not this checkout's `src/`.
"""

import ast
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _loaded(name):
    spec = importlib.util.spec_from_file_location(f"{name}_own", ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _two_checkouts(tmp_path):
    """Two checkouts, each with the package in its own src/."""
    here, other = tmp_path / "here", tmp_path / "other"
    for checkout in (here, other):
        (checkout / "src" / "gizmo").mkdir(parents=True)
        (checkout / "src" / "gizmo" / "__init__.py").write_text("")
    return here, other


def _must_not_run(*_args):
    raise AssertionError("the suite ran")


def test_a_mutation_run_measures_only_this_checkout_s_own_code(tmp_path, monkeypatch):
    here, other = _two_checkouts(tmp_path)
    (here / "src" / "gizmo" / "core.py").write_text("# RULE: the gizmo is on\non = True\n")
    rows = here / "scripts/mutations/src/gizmo/core.py"
    rows.mkdir(parents=True)
    (rows / "the-gizmo-is-on.toml").write_text(
        '[[rule]]\nname = "the gizmo is on"\nfile = "src/gizmo/core.py"\n'
        "find = '^on = True$'\nreplace = 'on = False'\n"
    )
    mutate = _loaded("mutate")
    for name, value in {
        "ROOT": here,
        "ROWS": here / "scripts/mutations",
        "RECOVERY": here / "scripts/.mutate-recovery.json",
        "MUTATE_TMP": tmp_path / "mutate-tmp",
        "PACKAGE": "gizmo",
        "run_suite": _must_not_run,
    }.items():
        monkeypatch.setattr(mutate, name, value)
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    monkeypatch.setenv("PYTHONPATH", str(other / "src"))  # the other checkout's install
    with pytest.raises(SystemExit) as refused:
        mutate.main()
    said = str(refused.value)
    assert str(other / "src" / "gizmo") in said
    assert str(here / "src") in said
    assert "uv sync" in said
    monkeypatch.setenv("PYTHONPATH", str(here / "src"))
    mutate.own_code(dict(os.environ))  # its own: no refusal


def test_the_ratchet_measures_only_the_code_of_the_tree_it_measures(tmp_path, monkeypatch):
    here, other = _two_checkouts(tmp_path)
    ratchet = _loaded("ratchet")
    monkeypatch.setattr(ratchet, "ROOT", here)
    monkeypatch.setattr(ratchet, "SOURCE", here / "src")
    monkeypatch.setattr(ratchet, "PACKAGE", here / "src" / "gizmo")
    monkeypatch.setattr(ratchet, "PYTHON", (sys.executable,))
    monkeypatch.setenv("PYTHONPATH", str(other / "src"))
    with pytest.raises(SystemExit) as refused:
        ratchet.measure()  # asks before it measures anything
    said = str(refused.value)
    assert str(other / "src" / "gizmo") in said
    assert str(here / "src") in said
    assert "uv sync" in said
    monkeypatch.setenv("PYTHONPATH", str(here / "src"))
    ratchet.own_code()


@pytest.mark.parametrize("script", ["mutate", "ratchet"])
def test_a_check_tool_never_imports_the_package_itself(script):
    """Its own import would answer for this process, not for the suite it is about to run.

    The ratchet runs a pull request's copy of itself in the base's environment, so an
    import here would even find the wrong tree's package.
    """
    tree = ast.parse((ROOT / f"scripts/{script}.py").read_text())
    nodes = list(ast.walk(tree))
    imports = [node for node in nodes if isinstance(node, ast.Import)]
    imported = {alias.name.split(".")[0] for node in imports for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in nodes if isinstance(node, ast.ImportFrom)}
    assert "ams_mcp" not in imported
