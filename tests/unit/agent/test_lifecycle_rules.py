import itertools

import pytest

from rw.agent import lifecycle_rules as rules

ALLOWED = {
    ("watching", "watch"): "watching",
    ("watching", "alert"): "alerted",
    ("watching", "resolve"): "resolved",
    ("watching", "false_alarm"): "closed",
    ("alerted", "alert"): "alerted",
    ("alerted", "approve"): "approved",
    ("alerted", "reject"): "rejected",
    ("approved", "confirm"): "confirmed",
    ("approved", "resolve"): "resolved",
    ("rejected", "close"): "closed",
    ("confirmed", "close"): "closed",
    ("resolved", "close"): "closed",
}


@pytest.mark.parametrize(("status", "event"), sorted(ALLOWED))
def test_allowed_transitions(status, event):
    assert rules.next_status(status, event) == ALLOWED[(status, event)]


DISALLOWED = sorted(set(itertools.product(rules.STATUSES, rules.EVENTS)) - set(ALLOWED))


@pytest.mark.parametrize(("status", "event"), DISALLOWED)
def test_every_other_transition_raises(status, event):
    with pytest.raises(
        rules.InvalidTransition, match=f"cannot {event} an incident that is {status}"
    ):
        rules.next_status(status, event)


def test_the_table_covers_the_diagram_and_nothing_else():
    table = {(s, e): n for s, moves in rules.TRANSITIONS.items() for e, n in moves.items()}
    assert table == ALLOWED
    assert set(rules.TRANSITIONS) == set(rules.STATUSES)


def test_closed_is_final_and_unknown_status_raises():
    with pytest.raises(rules.InvalidTransition, match=r"allowed: nothing \(final\)"):
        rules.next_status("closed", "alert")
    with pytest.raises(rules.InvalidTransition, match="unknown incident status 'lost'"):
        rules.next_status("lost", "alert")


def test_active_statuses_keep_follow_ups_coming():
    assert {s for s in rules.STATUSES if rules.is_active(s)} == {"watching", "alerted", "approved"}


def test_module_is_stdlib_only():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(rules.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__"}  # rw-api packages this file into a Lambda
