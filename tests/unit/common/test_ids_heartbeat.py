import re

import pytest

from rw.common.heartbeat import beat, heartbeat_path, last_beat
from rw.common.ids import PREFIXES, new_id
from rw.contracts.base import prefixed_id


@pytest.mark.parametrize("prefix", sorted(PREFIXES))
def test_new_id_matches_the_contract_pattern(prefix):
    value = new_id(prefix)
    assert re.match(prefixed_id(prefix), value), value


def test_new_ids_are_unique_and_rejects_unknown_prefix():
    assert len({new_id("res") for _ in range(1000)}) == 1000
    with pytest.raises(ValueError, match="unknown ID prefix 'cand'"):
        new_id("cand")


def test_beat_writes_epoch_to_configured_dir(env, tmp_path):
    env(RW_HEARTBEAT_DIR=str(tmp_path / "run"))
    assert last_beat("rw-ingest") is None
    path = beat("rw-ingest")
    assert path == tmp_path / "run" / "rw-ingest.heartbeat"
    first = last_beat("rw-ingest")
    assert first is not None and first > 1_700_000_000
    beat("rw-ingest")
    assert last_beat("rw-ingest") >= first
    assert [p.name for p in (tmp_path / "run").iterdir()] == ["rw-ingest.heartbeat"]


def test_beat_accepts_an_explicit_directory(tmp_path):
    assert beat("rw-agent", tmp_path) == heartbeat_path("rw-agent", tmp_path)
    assert last_beat("rw-agent", tmp_path) is not None
