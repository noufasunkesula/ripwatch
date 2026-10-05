"""Prefixed ULIDs for every RipWatch ID (sprint-1.md 7.1 field rules)."""

from __future__ import annotations

from typing import Literal, get_args

from ulid import ULID

IdPrefix = Literal["res", "tr", "job", "inc", "dec"]
PREFIXES: frozenset[str] = frozenset(get_args(IdPrefix))


def new_id(prefix: IdPrefix) -> str:
    """Return `<prefix>_<ULID>`, e.g. `res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F`. ULIDs sort by time."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown ID prefix {prefix!r}; allowed: {sorted(PREFIXES)}")
    return f"{prefix}_{ULID()}"
