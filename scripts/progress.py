"""Sprint 1 progress from the handoff.md task board and the sprint DoD (cloud-claude.md 6).

Run with `uv run python scripts/progress.py`. Same input files give the same output for everyone.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
HANDOFF = ROOT / "handoff.md"
SPRINT = ROOT / "docs" / "sprints" / "sprint-1.md"

SPRINT_START = date(2026, 10, 1)
SPRINT_END = date(2026, 10, 7)
SPRINT_DAYS = 7
LIGHTER_PATH_FROM = date(2026, 10, 4)

WEIGHTS = {"todo": 0.0, "in-progress": 0.25, "in-review": 0.75, "done": 1.0}
STATUSES = {*WEIGHTS, "cut"}

_ROW = re.compile(r"^\|\s*([NDJ]-\d{2})\s*\|[^|]*\|\s*([a-z-]+)\s*\|")
_BOX = re.compile(r"^\s*- \[([ xX])\]")


@dataclass(frozen=True)
class Group:
    """Weighted progress for one group of tasks; cut tasks are left out of `total`."""

    done: float
    total: int

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


def parse_board(text: str) -> dict[str, str]:
    """Task ID -> status from every task board row in handoff.md."""
    board: dict[str, str] = {}
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        task, status = match.groups()
        if status not in STATUSES:
            raise ValueError(f"{task}: unknown status {status!r} (allowed: {sorted(STATUSES)})")
        board[task] = status
    return board


def parse_dod(text: str) -> tuple[int, int]:
    """(checked, total) checkboxes under the "Definition of done" heading of the sprint file."""
    checked = total = 0
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = "definition of done" in line.lower()
            continue
        match = _BOX.match(line) if inside else None
        if match:
            total += 1
            checked += match.group(1) != " "
    return checked, total


def group(board: dict[str, str], prefix: str) -> Group:
    statuses = [s for task, s in board.items() if task.startswith(prefix) and s != "cut"]
    return Group(sum(WEIGHTS[s] for s in statuses), len(statuses))


def _num(value: float) -> str:
    return f"{value:g}"


def _days(value: float) -> str:
    rounded = round(value * 2) / 2
    return f"~{_num(rounded)} day" + ("" if rounded == 1 else "s")


def report(board: dict[str, str], dod: tuple[int, int], now: datetime) -> str:
    """Build the progress block shown at session start and end."""
    now = now.astimezone(IST)
    overall = group(board, "")
    elapsed = (now - datetime.combine(SPRINT_START, time(), IST)).total_seconds() / 86400
    expected = min(max(elapsed / SPRINT_DAYS, 0.0), 1.0)
    actual = overall.done / overall.total if overall.total else 0.0
    behind = (expected - actual) * SPRINT_DAYS
    days_left = max((SPRINT_END - now.date()).days, 0)

    if behind <= 0:
        on_track = f"Yes (expected {round(expected * 100)}% today)"
    else:
        on_track = f"Behind by {_days(behind)} (expected {round(expected * 100)}% today)"

    noufa, daksh, joint = group(board, "N-"), group(board, "D-"), group(board, "J-")
    lines = [
        f"Sprint 1 progress ({now.strftime('%a %d %b %Y, %H:%M')} IST)",
        f"  Overall tasks:      {overall.percent}%  ({_num(overall.done)} / {overall.total})",
        f"  Noufa (N-01..N-15): {noufa.percent}%  ({_num(noufa.done)} / {noufa.total})",
        f"  Daksh (D-01..D-09): {daksh.percent}%  ({_num(daksh.done)} / {daksh.total})",
        f"  Joint (J-01):       {joint.percent}%",
        f"  Definition of Done: {dod[0]} / {dod[1]} checked",
        f"  Days left:          {days_left} (sprint ends {SPRINT_END.strftime('%a %d %b %Y')})",
        f"  On track?           {on_track}",
    ]
    if behind > 1 and now.date() >= LIGHTER_PATH_FROM:
        lines.append("  Reminder: the lighter path (sprint-1.md 0.4) exists. Switch?")
    return "\n".join(lines)


def main() -> int:
    board = parse_board(HANDOFF.read_text(encoding="utf-8"))
    dod = parse_dod(SPRINT.read_text(encoding="utf-8"))
    print(report(board, dod, datetime.now(IST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
