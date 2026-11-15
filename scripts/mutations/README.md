# One row per rule

A rule nothing catches is a rule nothing protects. `scripts/mutate.py` turns
each rule off in turn and runs the suite; a rule whose removal breaks no test
has no test. `tests/test_rules_have_evidence.py` counts the rules in the source
against the rows here, so this folder cannot fall behind.

Each row is a file of its own, named after the rule, in a folder that is the
whole path of the file it switches off. `# RULE: a page is asked for at most
twice` in `src/ams_mcp/reader.py` goes in
`src/ams_mcp/reader.py/a-page-is-asked-for-at-most-twice.toml` here:

```toml
[[rule]]
name = "a page is asked for at most twice"
file = "src/ams_mcp/reader.py"
find = 'for _ in range\(TRIES\):'
replace = 'for _ in range(TRIES + 1):'
```

`find` is a regular expression matching the single line to replace; `replace`
is what replaces it, keeping the file valid. The line's own indentation is
kept, so do not write indentation here.

A rule in `scripts/` is proven the same way, the mutation runner's own
included. The runner has loaded its code before it switches a line off, and
the suite it starts loads the switched-off file. A rule inside `repair()`,
or in any function in `scripts/mutate.py` that it calls by plain name, could
not be proven like this, since a killed run repairs with the switched-off
code. Keep rules and rows out of them; `test_no_rule_lives_inside_repair`
checks both.

Two pull requests that each add a rule then never touch the same file, unless
they add the same rule. A row in the wrong folder, a file holding two rows,
and two rows for one rule are each refused by a test, which says where the row
belongs.
