"""Print the current time in IST, e.g. "Thursday 01 October 2026, 17:45 IST" (cloud-claude.md 3)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def format_ist(moment: datetime) -> str:
    """Format an aware datetime as weekday, date, year and time in IST."""
    return moment.astimezone(IST).strftime("%A %d %B %Y, %H:%M IST")


def main() -> None:
    print(format_ist(datetime.now(IST)))


if __name__ == "__main__":
    main()
