from datetime import datetime

import pytest

from scripts import progress
from scripts.now import format_ist
from scripts.progress import IST, Group, group, parse_board, parse_dod, report

BOARD = """
| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| N-01 | Repo scaffold | done | noufa/N-01 | #4 |
| N-02 | Tooling | in-progress | | |
| N-03 | Config | cut | | |
| D-01 | Contracts | in-review | daksh/D-01 | #1 |
| D-02 | Trace | todo | | |
| J-01 | Local demo | todo | | |

Statuses: `todo`, `in-progress`, `in-review`, `done`, `cut`.
"""

SPRINT = """
## 1. Goal

### Definition of done (all must be true)

- [x] Repo matches the layout
- [ ] make check passes
- [X] Contract agreed

## 2. Inputs

- [ ] not part of the DoD
"""


def test_parse_board_reads_every_task_row():
    board = parse_board(BOARD)
    assert board == {
        "N-01": "done",
        "N-02": "in-progress",
        "N-03": "cut",
        "D-01": "in-review",
        "D-02": "todo",
        "J-01": "todo",
    }


def test_parse_board_rejects_unknown_status():
    with pytest.raises(ValueError, match="N-01: unknown status 'finished'"):
        parse_board("| N-01 | Repo scaffold | finished | | |")


def test_parse_dod_counts_only_the_dod_section():
    assert parse_dod(SPRINT) == (2, 3)


def test_group_excludes_cut_tasks():
    board = parse_board(BOARD)
    assert group(board, "N-") == Group(1.25, 2)
    assert group(board, "D-") == Group(0.75, 2)
    assert group(board, "") == Group(2.0, 5)
    assert Group(0, 0).percent == 0


def test_report_on_track():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=IST)
    text = report(parse_board(BOARD), (2, 3), now)
    assert "Sprint 1 progress (Thu 01 Oct 2026, 12:00 IST)" in text
    assert "Overall tasks:      40%  (2 / 5)" in text
    assert "Noufa (N-01..N-15): 62%  (1.25 / 2)" in text
    assert "Daksh (D-01..D-09): 38%  (0.75 / 2)" in text
    assert "Joint (J-01):       0%" in text
    assert "Definition of Done: 2 / 3 checked" in text
    assert "Days left:          6 (sprint ends Wed 07 Oct 2026)" in text
    assert "On track?           Yes (expected 7% today)" in text
    assert "lighter path" not in text


def test_report_behind_reminds_lighter_path_from_oct_4():
    board = {"N-01": "todo", "D-01": "todo"}
    text = report(board, (0, 17), datetime(2026, 10, 5, 12, 0, tzinfo=IST))
    assert "Behind by ~4.5 days (expected 64% today)" in text
    assert "Days left:          2" in text
    assert "lighter path (sprint-1.md 0.4)" in text

    early = report(board, (0, 17), datetime(2026, 10, 3, 12, 0, tzinfo=IST))
    assert "Behind by ~2.5 days" in early
    assert "lighter path" not in early


def test_main_reads_the_real_repo_files(capsys):
    assert progress.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("Sprint 1 progress (")
    assert "Definition of Done:" in out


def test_format_ist_converts_to_ist():
    utc = datetime.fromisoformat("2026-10-01T12:15:00+00:00")
    assert format_ist(utc) == "Thursday 01 October 2026, 17:45 IST"
