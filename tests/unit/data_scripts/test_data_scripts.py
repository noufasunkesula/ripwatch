import json
import socket
from urllib.parse import unquote_plus

import boto3
import pytest
from moto.server import ThreadedMotoServer

from rw.common.config import get_settings
from scripts import download_ripvis, local_seed, make_replay_clips, upload_data

ACCOUNT = "123456789012"


@pytest.fixture(scope="module")
def moto_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def local_env(monkeypatch, moto_url):
    for key in ("AWS_PROFILE", "RW_CONFIRM_APPLY", "RW_DEMO_LAT", "RW_DEMO_LON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RW_AWS_ENDPOINT_URL", moto_url)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCOUNT_ID", ACCOUNT)
    get_settings.cache_clear()
    yield moto_url
    get_settings.cache_clear()


def _client(name, url):
    return boto3.client(name, endpoint_url=url, region_name="us-east-1")


# ---------------------------------------------------------------- local_seed


def test_local_seed_refuses_a_non_local_endpoint(monkeypatch):
    monkeypatch.setenv("RW_AWS_ENDPOINT_URL", "https://dynamodb.us-east-1.amazonaws.com")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="is not local"):
            local_seed.require_local_endpoint()
        monkeypatch.delenv("RW_AWS_ENDPOINT_URL")
        get_settings.cache_clear()
        with pytest.raises(SystemExit, match="is not local"):
            local_seed.require_local_endpoint()
    finally:
        get_settings.cache_clear()


def test_local_seed_creates_everything_and_is_idempotent(local_env, monkeypatch):
    monkeypatch.setenv("RW_DEMO_LAT", "30.17")
    monkeypatch.setenv("RW_DEMO_LON", "-85.80")
    first = local_seed.seed()
    second = local_seed.seed()
    assert first["queues"] == second["queues"]

    assert set(first["buckets"].values()) <= {
        b["Name"] for b in _client("s3", local_env).list_buckets()["Buckets"]
    }
    ddb = _client("dynamodb", local_env)
    assert set(ddb.list_tables()["TableNames"]) >= {
        "rw-cameras", "rw-jobs", "rw-detections", "rw-incidents", "rw-agent-trace", "rw-approvals"
    }  # fmt: skip
    incidents = ddb.describe_table(TableName="rw-incidents")["Table"]
    assert incidents["GlobalSecondaryIndexes"][0]["IndexName"] == "status-index"
    ttl = ddb.describe_time_to_live(TableName="rw-detections")["TimeToLiveDescription"]
    assert ttl["AttributeName"] == "expires_at"

    camera = (
        boto3.resource("dynamodb", endpoint_url=local_env, region_name="us-east-1")
        .Table("rw-cameras")
        .get_item(Key={"camera_id": "cam-01"})["Item"]
    )
    assert camera["replay_prefix"] == "replay/clips/cam-01/"
    assert [float(v) for v in camera["seaward_vector"]] == [0.0, -1.0]
    assert float(camera["lat"]) == pytest.approx(30.17)

    ssm = _client("ssm", local_env)
    assert ssm.get_parameter(Name="/rw/vision/rip_threshold")["Parameter"]["Value"] == "0.70"
    assert ssm.get_parameter(Name="/rw/release")["Parameter"]["Value"] == "none"

    sqs = _client("sqs", local_env)
    redrive = sqs.get_queue_attributes(QueueUrl=first["queues"]["jobs"], AttributeNames=["All"])
    assert json.loads(redrive["Attributes"]["RedrivePolicy"])["maxReceiveCount"] == 3


def test_upload_to_incoming_reaches_rw_jobs(local_env):
    summary = local_seed.seed()
    s3, sqs = _client("s3", local_env), _client("sqs", local_env)
    jobs = summary["queues"]["jobs"]
    sqs.purge_queue(QueueUrl=jobs)
    s3.put_object(Bucket=summary["buckets"]["data"], Key="incoming/cam-01/x.mp4", Body=b"v")
    s3.put_object(Bucket=summary["buckets"]["data"], Key="replay/clips/cam-01/y.mp4", Body=b"v")
    messages = sqs.receive_message(QueueUrl=jobs, MaxNumberOfMessages=10).get("Messages", [])
    # S3 URL-encodes keys in event messages (real AWS too); ingest must unquote_plus them.
    records = [r for m in messages for r in json.loads(m["Body"])["Records"]]
    assert [unquote_plus(r["s3"]["object"]["key"]) for r in records] == ["incoming/cam-01/x.mp4"]


# ---------------------------------------------------------------- upload_data


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "ripvis" / "train").mkdir(parents=True)
    (tmp_path / "ripvis" / "train" / "sampled_images.zip").write_bytes(b"z" * 10)
    (tmp_path / "ripvis" / "val" / "videos").mkdir(parents=True)
    (tmp_path / "ripvis" / "val" / "videos" / "v1.mp4").write_bytes(b"v" * 20)
    (tmp_path / "replay" / "clips" / "cam-01").mkdir(parents=True)
    (tmp_path / "replay" / "clips" / "cam-01" / "c_0001.mp4").write_bytes(b"c" * 5)
    return tmp_path


def test_upload_plan_maps_local_files_to_keys(data_dir):
    assert [k for _, k in upload_data.plan(data_dir)] == [
        "raw/ripvis/train/sampled_images.zip",
        "raw/ripvis/val/videos/v1.mp4",
        "replay/clips/cam-01/c_0001.mp4",
    ]


def test_upload_refuses_without_confirmation(local_env, data_dir, capsys):
    assert upload_data.main(["--data", str(data_dir)]) == 1
    assert "RW_CONFIRM_APPLY=upload-data" in capsys.readouterr().err


def test_upload_dry_run_needs_no_confirmation(data_dir, capsys):
    assert upload_data.main(["--data", str(data_dir), "--dry-run"]) == 0
    assert "3 files" in capsys.readouterr().out


def test_upload_skips_unchanged_files_and_writes_a_manifest(local_env, data_dir, monkeypatch):
    local_seed.seed()
    monkeypatch.setenv("RW_CONFIRM_APPLY", "upload-data")
    assert upload_data.main(["--data", str(data_dir)]) == 0
    assert upload_data.main(["--data", str(data_dir)]) == 0

    s3 = _client("s3", local_env)
    manifest = json.loads(
        s3.get_object(Bucket=f"rw-artifacts-{ACCOUNT}", Key="reports/data-manifest.json")[
            "Body"
        ].read()
    )
    assert manifest["uploaded"] == 0 and manifest["skipped_unchanged"] == 3
    assert manifest["prefixes"]["replay"] == {"files": 1, "bytes": 5}
    obj = s3.head_object(Bucket=f"rw-data-{ACCOUNT}", Key="raw/ripvis/val/videos/v1.mp4")
    assert obj["ContentLength"] == 20


# ---------------------------------------------------------------- make_replay_clips


def test_replay_ffmpeg_command_matches_the_spec(tmp_path):
    command = make_replay_clips.ffmpeg_command(tmp_path / "test" / "beach.mp4", tmp_path / "out")
    joined = " ".join(command)
    assert "-vf scale=960:-2 -r 15 -an -c:v libx264 -crf 23" in joined
    assert "-f segment -segment_time 10 -reset_timestamps 1" in joined
    assert command[-1].endswith("beach_%04d.mp4")


def test_replay_dry_run_prints_commands(tmp_path, capsys):
    config = tmp_path / "cams.yaml"
    config.write_text("cameras:\n  cam-01:\n    videos: [test/a.mp4, test/b.mp4]\n")
    assert make_replay_clips.main(["--config", str(config), "--dry-run"]) == 0
    assert capsys.readouterr().out.count("ffmpeg ") == 2


def test_replay_stops_until_saif_picks_videos(capsys):
    assert make_replay_clips.main(["--dry-run"]) == 1  # committed config has empty lists
    assert "Saif picks them" in capsys.readouterr().out


# ---------------------------------------------------------------- download_ripvis


def test_download_patterns_and_matching():
    patterns = download_ripvis.patterns_for(["val", "test"])
    files = {"val/videos/a.mp4": 5, "test/b.mp4": 7, "test/notes.txt": 1, "train/train.json": 2}
    assert download_ripvis.matching(files, patterns) == {"val/videos/a.mp4": 5, "test/b.mp4": 7}
    with pytest.raises(SystemExit, match="unknown parts"):
        download_ripvis.patterns_for(["everything"])


def test_download_dry_run_lists_without_downloading(monkeypatch, capsys):
    files = {"train/train.json": 1024, "train/other.zip": 9, "test/b.mp4": 2048}
    monkeypatch.setattr(download_ripvis, "list_files", lambda repo, token: files)
    assert download_ripvis.main(["--parts", "train,test", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("RipVIS: CC BY-NC 4.0, no redistribution.")
    assert "train/train.json" in out and "test/b.mp4" in out and "other.zip" not in out
    assert "2 files, 3.0 KB total" in out
