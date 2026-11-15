"""Every rule this project enforces has evidence, and the evidence names it.

A rule here is one line of source marked ``# RULE: what it says``. Three things
have to be true of each, and none of them is true by writing the rule:

* it has a row, in a file of its own under ``scripts/mutations/``, so
  something switches it off;
* a test carries the rule's own words as its name;
* switching it off turns *that* test red, which ``scripts/mutate.py`` checks.

The second is what makes the third readable. A mutation breaks whatever it
breaks, and a kill only means something when the test that went red is the one
that names the rule — otherwise a documented example that happened to change can
leave a rule looking guarded while nothing guards it.
"""

import ast
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$")


def _mutate():
    """The mutation runner, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marked_rules() -> set[str]:
    """Every rule the source marks, by the words the marker gives it."""
    return _mutate().marked_rules()


def rowed_rules() -> set[str]:
    """Every rule the mutation rows claim to switch off."""
    return {str(rule["name"]) for rule in _mutate().all_rows()}


def defined_test_names() -> set[str]:
    """Every test function this suite defines.

    Not called ``test_names``: pytest collects anything starting with ``test_``,
    and a helper that returns a set would be reported as a test that forgot to
    assert.
    """
    found = set()
    for module in sorted((ROOT / "tests").rglob("test_*.py")):
        found.update(re.findall(r"^(?:async )?def (test_\w+)", module.read_text(), re.MULTILINE))
    return found


def test_every_rule_in_the_source_has_a_row_in_the_mutation_table():
    """Without this the evidence falls behind the code and nobody sees it happen."""
    marked, rowed = marked_rules(), rowed_rules()
    assert marked - rowed == set(), f"marked in the source and never switched off: {marked - rowed}"
    assert rowed - marked == set(), f"a row for a rule no longer in the source: {rowed - marked}"


def test_every_rule_has_a_test_named_after_it():
    """A kill is only evidence about a rule when the test that went red names it."""
    name_for = _mutate().test_name_for
    named = defined_test_names()
    missing = sorted(rule for rule in marked_rules() if name_for(rule) not in named)
    wanted = ", ".join(f"{name_for(rule)}()" for rule in missing)
    assert not missing, f"rules with no test named after them — write {wanted}"


def _row(name, file="src/ams_mcp/reader.py"):
    return f"[[rule]]\nname = \"{name}\"\nfile = \"{file}\"\nfind = 'x'\nreplace = 'y'\n"


def _file(folder, source, name, text):
    (folder / source).mkdir(parents=True, exist_ok=True)
    (folder / source / name).write_text(text, encoding="utf-8")


def test_a_row_file_in_the_wrong_folder_is_refused(tmp_path):
    """One row per file, named after the rule, under the whole path of its source.

    Then where a row lives is never a choice, and two pull requests only meet
    when they add the same rule.
    """
    folder = tmp_path / "mutations"
    reader, service = "src/ams_mcp/reader.py", "src/ams_mcp/service.py"
    _file(folder, reader, "in-place.toml", _row("in place"))
    _file(folder, service, "wrong-folder.toml", _row("wrong folder"))
    _file(folder, reader, "a-wrong-name.toml", _row("named otherwise"))
    _file(folder, reader, "two-rows.toml", _row("two rows") + _row("in one file"))
    found = " ".join(_mutate().misfiled(folder))
    assert f"{service}/wrong-folder.toml should be {reader}/wrong-folder.toml" in found
    assert f"{reader}/a-wrong-name.toml should be {reader}/named-otherwise.toml" in found
    assert f"{reader}/two-rows.toml holds 2 rows" in found
    assert "in-place" not in found


def test_every_row_file_in_the_repository_is_where_its_rule_says():
    misfiled = _mutate().misfiled()
    assert not misfiled, "row files out of place: " + "; ".join(misfiled)


def test_two_rows_for_one_rule_are_refused(tmp_path):
    """A copied row would switch the rule off twice and count it twice.

    The refusal names both files.
    """
    folder = tmp_path / "mutations"
    _file(folder, "src/ams_mcp/reader.py", "twice.toml", _row("twice"))
    _file(folder, "src/ams_mcp/service.py", "twice.toml", _row("twice"))
    with pytest.raises(SystemExit) as refused:
        _mutate().all_rows(folder)
    said = str(refused.value)
    assert "src/ams_mcp/reader.py/twice.toml" in said
    assert "src/ams_mcp/service.py/twice.toml" in said
    assert "delete one" in said


def test_a_rule_whose_words_give_no_file_name_is_refused():
    """A name with no letter or digit would be filed as ".toml".

    The next such name would land on top of it.
    """
    with pytest.raises(SystemExit, match="letter"):
        _mutate().row_path({"name": "?!", "file": "src/ams_mcp/reader.py"})


def test_a_row_s_folder_is_the_whole_path_of_the_file_it_switches_off():
    """The same layout as the bridge and the other voice plugins."""
    row_path = _mutate().row_path
    rule = {"name": "a page is asked for at most twice", "file": "src/ams_mcp/reader.py"}
    assert str(row_path(rule)) == "src/ams_mcp/reader.py/a-page-is-asked-for-at-most-twice.toml"
    assert str(row_path({**rule, "file": "scripts/ratchet.py"})).startswith("scripts/ratchet.py/")


def test_there_is_no_shared_table_left():
    """Every row moved into the folder; a table beside it would be read by nobody."""
    assert not (ROOT / "scripts/mutations.toml").exists()


def test_every_row_finds_the_line_it_switches_off():
    """A row whose line was rewritten is only found partway through a long run."""
    mutate = _mutate()
    lost = []
    for rule in mutate.all_rows():
        try:
            mutate.line_of((ROOT / rule["file"]).read_text(), rule["find"], rule.get("occurrence", 1))
        except SystemExit:
            lost.append(f"{rule['name']} ({rule['file']})")
    assert not lost, "rows that find no line: " + "; ".join(lost)


def test_a_rule_in_any_script_is_read(tmp_path):
    """A fixed list of scripts missed any tool added later, and its rules went unproven."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "core.py").write_text("# RULE: a rule in the product\nx = 1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a_new_tool.py").write_text("    # RULE: a rule in a new tool\n")
    # A row folder is named after its script, so a folder called a_new_tool.py is no script.
    (tmp_path / "scripts" / "mutations" / "scripts" / "a_new_tool.py").mkdir(parents=True)
    assert _mutate().marked_rules(tmp_path) == {"a rule in the product", "a rule in a new tool"}


def test_no_rule_sits_inside_repair():
    """repair() puts back a file a killed run left switched off, before anything else runs.

    A rule inside it would be proven by running the switched-off repair, so the
    proof would repair nothing. Keep rules out of it.
    """
    source = (ROOT / "scripts" / "mutate.py").read_text()
    [repair] = [
        n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "repair"
    ]
    inside = source.splitlines()[repair.lineno - 1 : repair.end_lineno]
    assert not [line for line in inside if MARKER.search(line)]
