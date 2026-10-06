"""Download the parts of RipVIS we use from Hugging Face into data/ripvis/ (sprint-1.md N-13).

    uv run python scripts/download_ripvis.py --dry-run            # list files and total size only
    uv run python scripts/download_ripvis.py --parts train,val,test

RipVIS (Dumitriu et al., CVPR 2025) is CC BY-NC 4.0 with no redistribution: data/ is gitignored,
and the data must never be committed, uploaded anywhere public, or shared outside the team.
Reads HF_TOKEN from the environment if the dataset page asks you to accept its terms.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

DEFAULT_REPO = "Irikos/RipVIS"
LICENSE_LINE = "RipVIS: CC BY-NC 4.0, no redistribution. Never commit or publish this data."

# What each part fetches (north star 0: evaluate on val, replay test videos as live cameras).
PATTERNS: dict[str, list[str]] = {
    "train": ["train/sampled_images.zip", "train/yolo_annotations.zip", "train/train.json"],
    "val": ["val/*"],
    "test": ["test/*.mp4"],
}


def patterns_for(parts: list[str]) -> list[str]:
    unknown = set(parts) - set(PATTERNS)
    if unknown:
        raise SystemExit(f"unknown parts {sorted(unknown)}; choose from {sorted(PATTERNS)}")
    return [p for part in parts for p in PATTERNS[part]]


def matching(files: dict[str, int], patterns: list[str]) -> dict[str, int]:
    """Files (path -> bytes) that match any allow pattern, the way snapshot_download filters."""
    return {f: size for f, size in files.items() if any(fnmatch.fnmatch(f, p) for p in patterns)}


def list_files(repo: str, token: str | None) -> dict[str, int]:
    from huggingface_hub import HfApi

    info = HfApi(token=token).dataset_info(repo, files_metadata=True)
    return {s.rfilename: int(s.size or 0) for s in info.siblings or []}


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download RipVIS parts into data/ripvis/")
    parser.add_argument("--parts", default="train,val,test", help="comma list of train,val,test")
    parser.add_argument("--dry-run", action="store_true", help="list files and total size only")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--out", default="data/ripvis")
    args = parser.parse_args(argv)

    print(LICENSE_LINE)
    token = os.environ.get("HF_TOKEN") or None
    patterns = patterns_for([p.strip() for p in args.parts.split(",") if p.strip()])
    selected = matching(list_files(args.repo, token), patterns)
    if not selected:
        print(f"No files in {args.repo} match {patterns}. Check the repo layout.", file=sys.stderr)
        return 1
    for path, size in sorted(selected.items()):
        print(f"  {human(size):>10}  {path}")
    print(f"{len(selected)} files, {human(sum(selected.values()))} total")
    if args.dry_run:
        return 0

    from huggingface_hub import snapshot_download

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.repo,
        repo_type="dataset",
        allow_patterns=patterns,
        local_dir=out,
        token=token,
    )
    print(f"Downloaded to {out}. {LICENSE_LINE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
