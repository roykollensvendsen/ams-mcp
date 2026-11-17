# SPDX-License-Identifier: GPL-3.0-or-later
"""A mutation run leaves no temporary files behind.

`scripts/mutate.py` runs the whole suite once per rule, and every run made its
own pytest folder of temporary files. Over a day of runs, folders like these
used up the machine's temporary space (about 756,000 of /tmp's million files,
on 13 November), until no program could start a command. Each run now gets a
folder of its own, outside the checkout, removed when the run ends. Whatever
a killed run left is swept away at the next start.
"""

import importlib.util
import pathlib
import sys

import pytest
from bounded import WITHIN

ROOT = pathlib.Path(__file__).parent.parent

LEAVES_FILES = """
import os

def test_it_writes_files_it_cannot_delete(tmp_path):
    (tmp_path / "plain.txt").write_text("x")
    (tmp_path / "locked.txt").write_text("x")
    os.chmod(tmp_path / "locked.txt", 0)
    (tmp_path / "shut").mkdir()
    (tmp_path / "shut" / "inside.txt").write_text("x")
    os.chmod(tmp_path / "shut", 0)
"""


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutate(tmp_path, monkeypatch):
    module = _load("mutate_temp")
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_leaves_files.py").write_text(LEAVES_FILES)
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def test_a_suite_run_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    failed, _ = mutate.run_suite(timeout=WITHIN)
    assert failed == 0
    assert list(mutate.MUTATE_TMP.iterdir()) == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows needs extra rights to make a link, and has no 0755"
)
def test_removing_a_run_s_files_never_changes_a_folder_it_links_to(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "kept.txt").write_text("x")
    (mutate.ROOT / "tests" / "test_links_out.py").write_text(
        f"def test_it_links_out(tmp_path):\n    (tmp_path / 'link').symlink_to({str(outside)!r})\n"
    )
    failed, _ = mutate.run_suite(timeout=WITHIN)
    assert failed == 0
    assert (outside.stat().st_mode & 0o777, (outside / "kept.txt").exists()) == (0o755, True)


def test_a_killed_run_s_temporary_files_are_swept_at_the_next_start(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    left = mutate.MUTATE_TMP / "run-left-by-a-killed-run"
    (left / "shut").mkdir(parents=True)
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    mutate.sweep()
    assert not left.exists()


def test_the_temporary_files_live_outside_the_checkout():
    """Inside it, a test that makes a git repository of its own would find this one instead."""
    assert ROOT.resolve() not in _load("mutate_where").MUTATE_TMP.resolve().parents


def test_a_passing_test_keeps_no_temporary_files():
    settings = (ROOT / "pyproject.toml").read_text()
    assert 'tmp_path_retention_policy = "failed"' in settings
    assert "tmp_path_retention_count = 1" in settings
