# SPDX-License-Identifier: GPL-3.0-or-later
"""The continuous-integration workflows, checked as the text GitHub reads.

Copied from claude-voice's tests/test_workflows.py (claude-voice #362), with the
group keyed by run for everything but a pull request (no-duplicates-v1 notes, n1).
"""

import pathlib

ROOT = pathlib.Path(__file__).parent.parent

#: A newer push to a pull request stops that pull request's stale run. Every other run,
#: on main, a tag or by hand, is its own group and always runs to the end. With one group
#: per ref, GitHub keeps only one waiting run, so a commit on main between two others got none.
SUPERSEDED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""


def test_every_pull_request_workflow_cancels_a_superseded_run_but_never_one_on_main():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    on_pull_requests = [p for p in workflows if "\n  pull_request:" in p.read_text(encoding="utf-8")]
    assert on_pull_requests, "no workflow runs on pull requests"
    missing = [p.name for p in on_pull_requests if SUPERSEDED not in p.read_text(encoding="utf-8")]
    assert missing == [], "give these the concurrency block in tests/test_workflows.py: " + ", ".join(missing)
