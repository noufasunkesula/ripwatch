"""rw-ocean-poller (sprint-1.md D-08) with recorded NOAA and NWS responses (no network).

Fixtures in tests/fixtures/ocean/ were recorded on 2026-10-06 for NOAA station 8729108 and NWS
zone FLZ112 (a real Rip Current Statement was active). The `network` test calls the live APIs and
only runs with RW_RUN_NETWORK=1.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from rw.mcp_tools.tools.ocean import OceanSnapshot

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "lambdas/ocean_poller"
FIXTURES = ROOT / "tests/fixtures/ocean"
NOW = datetime(2026, 10, 6, 9, 30, tzinfo=UTC)
PARAM = "/rw/ocean/latest"


@pytest.fixture(scope="module")
def poller():
    """handler, noaa and nws imported as Lambda does (bare names), under unique module names."""
    sys.path.insert(0, str(SOURCE))
    saved = {n: sys.modules.pop(n) for n in ("noaa", "nws") if n in sys.modules}
    try:
        spec = importlib.util.spec_from_file_location("rw_ocean_handler", SOURCE / "handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(SOURCE))
        for name in ("noaa", "nws"):
            sys.modules.pop(name, None)
        sys.modules.update(saved)


@pytest.fixture
def ssm(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL", "RW_SSM_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("RW_NOAA_STATION_ID", "8729108")
    monkeypatch.setenv("RW_NWS_ZONE_ID", "FLZ112")
    monkeypatch.setenv("RW_NWS_USER_AGENT", "RipWatch/0.1 (test@example.com)")
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


def _fixture(name: str):
    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return lambda *args: body


def _fail(*args):
    raise TimeoutError("timed out")


def _stored(ssm) -> tuple[OceanSnapshot, dict]:
    parameter = ssm.get_parameter(Name=PARAM)["Parameter"]
    return OceanSnapshot.model_validate_json(parameter["Value"]), parameter


def test_snapshot_from_recorded_responses(poller, ssm):
    poller.poll(NOW, _fixture("noaa_8729108.json"), _fixture("nws_FLZ112.json"))

    snapshot, parameter = _stored(ssm)
    assert len(parameter["Value"].encode()) < 4096
    assert snapshot.station == "8729108" and snapshot.errors == []
    assert [(t.type.value, t.height_m) for t in snapshot.tides] == [
        ("H", 0.526), ("L", 0.149), ("H", 0.47), ("L", 0.23),
    ]  # fmt: skip
    assert snapshot.tides[0].ts == datetime(2026, 10, 6, 12, 24, tzinfo=UTC)
    assert snapshot.tide_trend.value == "rising"  # next tide is high
    (alert,) = snapshot.alerts
    assert alert.event == "Rip Current Statement" and len(alert.headline) <= 160
    assert alert.expires is not None


def test_only_future_tides_and_trend_follows_the_next_one(poller, ssm):
    later = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)  # after the 12:24 high
    snapshot = poller.poll(later, _fixture("noaa_8729108.json"), _fixture("nws_FLZ112.json"))
    assert snapshot["tides"][0]["type"] == "L" and snapshot["tide_trend"] == "falling"
    assert len(snapshot["tides"]) == 3


def test_beach_alerts_are_filtered_capped_and_truncated(poller):
    feature = {"properties": {"event": "Rip Current Statement", "headline": "x" * 300,
                              "expires": "2026-10-07T04:00:00-04:00"}}  # fmt: skip
    body = {"features": [
        {"properties": {"event": "Flood Watch", "headline": "flood"}},
        {"properties": {"event": "High Surf Advisory", "headline": "surf"}},
        feature, feature, feature,
    ]}  # fmt: skip
    alerts = sys.modules["nws"].parse(body)
    assert [a["event"] for a in alerts] == [
        "High Surf Advisory", "Rip Current Statement", "Rip Current Statement",
    ]  # fmt: skip
    assert len(alerts[1]["headline"]) == 160


def test_partial_failure_keeps_the_last_good_section(poller, ssm):
    poller.poll(NOW, _fixture("noaa_8729108.json"), _fixture("nws_FLZ112.json"))

    poller.poll(NOW, _fail, lambda *a: {"features": []})
    snapshot, _ = _stored(ssm)
    assert len(snapshot.tides) == 4 and snapshot.alerts == []
    assert snapshot.errors == ["noaa: TimeoutError: timed out"]

    poller.poll(NOW, _fixture("noaa_8729108.json"), _fail)
    snapshot, _ = _stored(ssm)
    assert snapshot.alerts == [] and snapshot.errors == ["nws: TimeoutError: timed out"]


def test_first_run_with_both_down_still_writes_a_valid_snapshot(poller, ssm):
    poller.poll(NOW, _fail, _fail)
    snapshot, _ = _stored(ssm)
    assert snapshot.tides == [] and snapshot.tide_trend.value == "unknown"
    assert [e.split(":")[0] for e in snapshot.errors] == ["noaa", "nws"]


def test_noaa_error_body_counts_as_a_failure(poller, ssm):
    poller.poll(NOW, lambda *a: {"error": {"message": "No Predictions data was found"}},
                _fixture("nws_FLZ112.json"))  # fmt: skip
    snapshot, _ = _stored(ssm)
    assert snapshot.errors == ["noaa: ValueError: NOAA error: No Predictions data was found"]


def test_snapshot_stays_under_4_kb(poller, ssm):
    long = {"properties": {"event": "Rip Current Statement " + "y" * 3000, "headline": "h"}}
    poller.poll(NOW, _fixture("noaa_8729108.json"), lambda *a: {"features": [long] * 3})
    _, parameter = _stored(ssm)
    assert len(parameter["Value"].encode()) < 4096


def test_request_urls_and_headers(poller, monkeypatch):
    noaa, nws = sys.modules["noaa"], sys.modules["nws"]
    assert noaa.url("8729108", NOW) == (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions"
        "&station=8729108&datum=MLLW&units=metric&time_zone=gmt&interval=hilo&format=json"
        "&begin_date=20261006&range=48&application=RipWatch"
    )
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b'{"features": []}'

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
        return Response()

    monkeypatch.setattr(nws.urllib.request, "urlopen", fake_urlopen)
    assert nws.fetch("FLZ112", "RipWatch/0.1 (test@example.com)") == {"features": []}
    assert seen["url"] == "https://api.weather.gov/alerts/active?zone=FLZ112"
    assert seen["headers"]["User-agent"] == "RipWatch/0.1 (test@example.com)"
    assert seen["headers"]["Accept"] == "application/geo+json" and seen["timeout"] == 10


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("RW_RUN_NETWORK") != "1", reason="set RW_RUN_NETWORK=1")
def test_live_apis_for_the_demo_station_and_zone(poller, ssm, monkeypatch):
    monkeypatch.setenv("RW_NWS_USER_AGENT", "RipWatch/0.1 (dakshsawhney2@gmail.com)")
    snapshot = poller.poll(datetime.now(UTC))
    assert snapshot["errors"] == [] and len(snapshot["tides"]) >= 2


@pytest.mark.parametrize("module", ["handler", "noaa", "nws"])
def test_poller_imports_only_stdlib_boto3_and_its_own_modules(module):
    import ast

    tree = ast.parse((SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    used = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names}  # fmt: skip
    used |= {n.module.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}  # fmt: skip
    allowed = set(sys.stdlib_module_names) | {"boto3", "botocore", "noaa", "nws"}
    assert used <= allowed, used - allowed
