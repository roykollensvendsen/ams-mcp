"""What the mutation runner does to the tree, and what it refuses to do to it.

This script edits source files and puts them back. Both halves can go wrong in
ways nobody notices until work is gone: it can restore over an edit somebody
made while the suite was running, and it can be killed holding a file open in
its switched-off state.
"""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent


def loaded():
    """The runner, imported by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated(mutate, tmp_path, monkeypatch, rows):
    """Point everything main() touches at tmp_path, so it never meets a real run.

    Each of these is set when the runner is loaded, so patching ROOT moves none of
    them. main() repairs from RECOVERY first: inside a mutation run the real record
    names the file being switched off, and a test reading it would put that file back.
    """
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "ROWS", rows)
    monkeypatch.setattr(mutate, "RECOVERY", tmp_path / "recovery.json")
    # main() sweeps MUTATE_TMP at start. Inside a mutation run the real one holds the
    # outer run's --basetemp, and sweeping it pulls other tests' tmp_path from under them.
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    monkeypatch.setattr("sys.argv", ["mutate.py"])


def test_a_file_untouched_since_the_mutation_is_put_back(tmp_path):
    mutate = loaded()
    source = tmp_path / "rule.py"
    source.write_text("if guard:\n")
    assert mutate.restore(source, "if guard:\n", "if guard:\n") is True


def test_a_file_changed_during_the_run_is_never_restored_over(tmp_path):
    """Losing somebody's edit costs more than losing the run."""
    mutate = loaded()
    source = tmp_path / "rule.py"
    source.write_text("what somebody wrote while the suite ran\n")
    assert mutate.restore(source, "the original\n", "the mutated version\n") is False
    assert source.read_text() == "what somebody wrote while the suite ran\n"


def test_a_run_that_was_killed_is_repaired_by_the_next_one(tmp_path, monkeypatch, capsys):
    mutate = loaded()
    source = tmp_path / "rule.py"
    source.write_text("if False:\n")
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps({"file": "rule.py", "original": "if guard:\n", "mutated": "if False:\n"}))
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "RECOVERY", recovery)
    mutate.repair()
    assert source.read_text() == "if guard:\n"
    assert not recovery.exists()
    assert "repaired" in capsys.readouterr().out


def test_a_coloured_failure_line_is_still_attributed(tmp_path, monkeypatch):
    """Colour around FAILED hides it, and a kill nobody can attribute is wasted.

    Without this the runner reports every rule as killed by some other test,
    which reads as "no rule has evidence" however well each one is guarded.
    """
    mutate = loaded()
    coloured = (
        "\x1b[31mFAILED\x1b[0m tests/test_thing.py::test_a_rule_holds - AssertionError\n"
        "\x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[32m5 passed\x1b[0m\n"
    )

    class Result:
        stdout = coloured
        stderr = ""

    monkeypatch.setattr(mutate.subprocess, "run", lambda *_a, **_k: Result())
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")  # never the real one
    count, failed = mutate.run_suite()
    assert count == 1
    assert failed == ["test_a_rule_holds"]


def test_two_mutations_never_share_a_bytecode_cache(tmp_path, monkeypatch):
    """Equal-length replacements make equal-sized files, and .pyc keys on size.

    CPython validates a cached .pyc from the source's modification time and
    size, both at one-second resolution. Two mutations inside the same second
    let the second run import the first one's bytecode, so the runner measures
    a rule that is not the one it switched off — and a false kill claims
    evidence for a rule nothing protects.
    """
    mutate = loaded()
    # The outer run of this very suite sets the variable, and a mutated
    # `{**os.environ}` would inherit it -- so the test would pass while
    # measuring nothing. Clear it and let the runner put it back itself.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("VALUE = 1\n")
    (project / "test_it.py").write_text("from pkg import VALUE\n\n\ndef test_it():\n    assert VALUE\n")
    monkeypatch.setattr(mutate, "ROOT", project)
    # Its own folder, not the real MUTATE_TMP: while a mutation switches off the clean-up,
    # a run there would be left in the folder the outer mutation run is using.
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    mutate.run_suite()
    assert not list(project.rglob("__pycache__")), (
        "the suite wrote bytecode, so two equal-sized mutations can collide"
    )


def test_an_empty_rule_table_is_not_a_crash(tmp_path, monkeypatch, capsys):
    """A scaffolded repository starts with a table of comments and no rules.

    Making the runner a required check is the point of it, and a traceback on
    an empty table means the first thing a new repository does with its own
    gate is fail on it.
    """
    mutate = loaded()
    rows = tmp_path / "mutations"
    rows.mkdir()
    (rows / "README.md").write_text("# One row per rule\n")
    isolated(mutate, tmp_path, monkeypatch, rows)
    assert mutate.main() == 0
    assert "no rules" in capsys.readouterr().out.lower()


def test_no_rows_when_rules_are_marked_is_a_failure(tmp_path, monkeypatch, capsys):
    """Rows deleted by mistake would leave every marked rule unproven and the run green."""
    mutate = loaded()
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "core.py").write_text("# RULE: a rule with no row\nx = 1\n")
    rows = tmp_path / "mutations"
    rows.mkdir()
    isolated(mutate, tmp_path, monkeypatch, rows)
    assert mutate.main() == 1
    said = capsys.readouterr().out
    assert "1 rule is marked" in said
    assert "git checkout origin/main -- scripts/mutations" in said
