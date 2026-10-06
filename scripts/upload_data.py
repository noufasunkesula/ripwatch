"""Upload RipVIS train/val and the replay clips to rw-data (sprint-1.md N-13). Sprint 2.

    uv run python scripts/upload_data.py --dry-run                     # counts only, no writes
    RW_CONFIRM_APPLY=upload-data uv run python scripts/upload_data.py   # after a human says so

data/ripvis/{train,val} -> s3://rw-data-<acct>/raw/ripvis/{train,val}/
data/replay/            -> s3://rw-data-<acct>/replay/
Skips objects that already exist with the same size. Writes a manifest with file counts and bytes
to s3://rw-artifacts-<acct>/reports/data-manifest.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rw.common.aws import client
from rw.common.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
CONFIRM = "upload-data"


@dataclass(frozen=True)
class Mapping:
    local: Path
    prefix: str


def mappings(data_root: Path) -> list[Mapping]:
    return [
        Mapping(data_root / "ripvis" / "train", "raw/ripvis/train/"),
        Mapping(data_root / "ripvis" / "val", "raw/ripvis/val/"),
        Mapping(data_root / "replay", "replay/"),
    ]


def plan(data_root: Path) -> list[tuple[Path, str]]:
    """(local file, s3 key) for every file to consider, in a stable order."""
    pairs = []
    for m in mappings(data_root):
        if not m.local.exists():
            continue
        for path in sorted(p for p in m.local.rglob("*") if p.is_file()):
            pairs.append((path, m.prefix + path.relative_to(m.local).as_posix()))
    return pairs


def remote_size(s3, bucket: str, key: str) -> int | None:  # noqa: ANN001
    try:
        return int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except s3.exceptions.ClientError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload RipVIS and replay clips to rw-data")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    pairs = plan(args.data)
    total = sum(p.stat().st_size for p, _ in pairs)
    print(f"{len(pairs)} files, {total / 1e9:.2f} GB under {args.data}")
    if args.dry_run:
        for prefix in sorted({k.split("/")[0] + "/" for _, k in pairs}):
            print(f"  {prefix}: {sum(1 for _, k in pairs if k.startswith(prefix))} files")
        return 0
    if os.environ.get("RW_CONFIRM_APPLY") != CONFIRM:
        print(
            f"Refused: uploading changes AWS. Re-run with RW_CONFIRM_APPLY={CONFIRM} after a human "
            "says so (sprint-1.md section 0).",
            file=sys.stderr,
        )
        return 1
    if not pairs:
        print("Nothing to upload: run download_ripvis.py and make_replay_clips.py first.")
        return 1

    settings = get_settings()
    data_bucket = settings.require("data_bucket")
    artifacts_bucket = settings.require("artifacts_bucket")
    s3 = client("s3")
    uploaded = skipped = 0
    counts: dict[str, dict[str, int]] = {}
    for path, key in pairs:
        size = path.stat().st_size
        top = "/".join(key.split("/")[:3]) if key.startswith("raw/") else "replay"
        bucket_count = counts.setdefault(top, {"files": 0, "bytes": 0})
        bucket_count["files"] += 1
        bucket_count["bytes"] += size
        if remote_size(s3, data_bucket, key) == size:
            skipped += 1
            continue
        s3.upload_file(str(path), data_bucket, key)
        uploaded += 1

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "bucket": data_bucket,
        "uploaded": uploaded,
        "skipped_unchanged": skipped,
        "prefixes": counts,
    }
    s3.put_object(
        Bucket=artifacts_bucket,
        Key="reports/data-manifest.json",
        Body=json.dumps(manifest, indent=2).encode(),
        ContentType="application/json",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
