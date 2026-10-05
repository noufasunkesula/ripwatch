import io
import json

from scripts import plan_summary
from scripts.plan_summary import render, summarize


def _rc(address: str, kind: str, *actions: str, mode: str = "managed") -> dict:
    return {"address": address, "type": kind, "mode": mode, "change": {"actions": list(actions)}}


PLAN = {
    "resource_changes": [
        _rc("aws_s3_bucket.state", "aws_s3_bucket", "create"),
        _rc("aws_iam_role.gha", "aws_iam_role", "create"),
        _rc("aws_sns_topic.ops", "aws_sns_topic", "update"),
        _rc("aws_instance.old", "aws_instance", "delete", "create"),
        _rc("aws_iam_policy.x", "aws_iam_policy", "no-op"),
        _rc("data.aws_caller_identity.current", "aws_caller_identity", "read", mode="data"),
    ]
}


def test_summarize_counts_actions_and_costly_creates():
    counts, costly = summarize(PLAN)
    assert (counts["add"], counts["change"], counts["destroy"]) == (3, 1, 1)
    assert [a for a, _ in costly] == ["aws_s3_bucket.state", "aws_instance.old"]


def test_render_lists_costs_and_warns_on_destroy():
    text = render("bootstrap", PLAN)
    assert text.startswith("Plan summary for bootstrap: 3 to add, 1 to change, 1 to destroy")
    assert "  - aws_instance.old: hourly compute" in text
    assert "WARNING: this plan destroys resources." in text


def test_render_empty_plan():
    text = render("network", {})
    assert "0 to add, 0 to change, 0 to destroy" in text
    assert "Resources that cost money: none" in text
    assert "WARNING" not in text


def test_main_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(PLAN)))
    assert plan_summary.main(["plan_summary.py", "data"]) == 0
    assert "Plan summary for data:" in capsys.readouterr().out
