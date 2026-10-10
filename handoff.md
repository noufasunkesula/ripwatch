# RipWatch Handoff

Shared session log for Sprint 1. Rules in `cloud-claude.md` sections 6 to 8. Append only. Never delete another person's lines.

---

## Task board: Noufa

| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| N-01 | Repo scaffold | in-review | noufa/N-01-repo-scaffold | #4 |
| N-02 | Tooling | done | noufa/N-01-repo-scaffold | #4 |
| N-03 | Config and environment | done | noufa/N-01-repo-scaffold | #4 |
| N-04 | `rw.common` | done | noufa/N-01-repo-scaffold | #4 |
| N-05 | Makefile | done | noufa/N-01-repo-scaffold | #4 |
| N-06 | Terraform bootstrap | done | noufa/N-01-repo-scaffold | #4 |
| N-07 | Terraform modules | done | noufa/N-01-repo-scaffold | #4 |
| N-08 | Stacks: iam, network, data | in-review | noufa/N-08-stacks-core | #5 |
| N-09 | Stacks: compute, serverless, edge, observability | in-review | noufa/N-08-stacks-core | #5 |
| N-10 | Worker runtime | in-review | noufa/N-10-worker-runtime | #6 |
| N-11 | Adapters, ingest, camera-sim, baseline vision | in-progress | noufa/N-11-vision | |
| N-12 | Lambdas: scheduler, kill switch | in-review | noufa/N-12-lambdas | #7 |
| N-13 | Data scripts | in-review | noufa/N-13-data-scripts | #8 |
| N-14 | Bench harness | todo | | |
| N-15 | CI | in-review | noufa/N-15-ci | #9 |

## Task board: Daksh

| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| D-01 | Contracts | todo | | |
| D-02 | Trace and decision helpers | todo | | |
| D-03 | MCP server and data tools | todo | | |
| D-04 | MCP action tools | todo | | |
| D-05 | Agent loop | todo | | |
| D-06 | Incident lifecycle and watching | todo | | |
| D-07 | `rw-api` Lambda | todo | | |
| D-08 | `rw-ocean-poller` Lambda | todo | | |
| D-09 | Local integration tests | todo | | |

## Task board: Joint

| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| J-01 | Local end-to-end demo | todo | | |

Statuses: `todo`, `in-progress`, `in-review`, `done`, `cut`.

---

## Requests

### For Noufa
- (none)

### For Daksh
- (from noufa, 2026-10-05) Open a PR from `daksh/D-03-mcp-tools` to `main` (it carries D-02 and D-03; #2 and #3 merged into stacked bases). N-11 ingest writes `rw-detections` through `rw.mcp_tools.store.to_item()`, so ingest is blocked until it lands. Push the local `8d639ec` first; resolve `sprint-1.md` into `docs/sprints/sprint-1.md` and keep both sides of `handoff.md`.
- (from noufa, 2026-10-05) S3 event keys arrive URL-encoded (`incoming%2Fcam-01%2Fx.mp4`, moto and AWS): anything reading `rw-jobs` events must `urllib.parse.unquote_plus` them.
- (from noufa, 2026-10-05) `rw-api` packaging (#5, serverless stack) picks up `rw/contracts/enums.py` and `rw/agent/lifecycle_rules.py` automatically once they exist (D-07).
- (from noufa, 2026-10-05) SSM `/rw/risk/thresholds` is created as `{}` and owned by D-05 (sprint-1.md 9.5 still missing); `/rw/noaa/station_id` and `/rw/nws/zone_id` are `unset` until the demo beach is chosen (D-08). `local_seed.py` takes beach coordinates from `RW_DEMO_LAT` / `RW_DEMO_LON`.
- (from noufa, 2026-10-05) `VisionPipeline.recheck(crops, result) -> float` implements your D-03 `Rechecker` protocol (on `noufa/N-11-vision`); wire it into `rw-mcp-tools` once both are on main.

---

## Session log: Noufa

### Session 1: noufa
- Start: Monday 05 October 2026, 14:28 IST (scripts/now.py missing, time from system clock)
- End: Monday 05 October 2026, 16:46 IST (2 h 18 min)
- Operator: Daksh, working Noufa's tasks while Noufa is unavailable (Decision Log 2026-10-05)
- Branches: noufa/N-01-repo-scaffold (#4, merged), noufa/N-08-stacks-core (#5), noufa/N-10-worker-runtime (#6), noufa/N-12-lambdas (#7), noufa/N-13-data-scripts (#8), noufa/N-15-ci (#9), noufa/N-11-vision (local only)
- Tasks: N-02 to N-07 (todo -> done, #4 merged by Noufa); N-01 (todo -> in-review, GitHub settings and 01-project-description.md open); N-08, N-09, N-10, N-12, N-13, N-15 (todo -> in-review); N-11 (todo -> in-progress, steps 1 and 2 of 3)
- Done:
  - Workflow files: scripts/now.py, scripts/progress.py (tested); docs moved into docs/ per section 4
  - N-02 to N-05: pyproject + uv.lock, ruff/pre-commit/tflint/checkov config, Settings, .env.example, rw.common (logging, metrics, aws, runtime, ids, heartbeat), full Makefile with apply guards, scripts/tf.sh, plan cost summary
  - N-06 to N-09: bootstrap, 8 modules, 7 stacks; tf validate (16 configurations) and checkov (487 passed, 0 failed) green
  - N-10: deploy/ user data, rw_deploy.sh, 5 systemd units, logrotate, OpenCV constraints, CloudWatch agent config, SSM document; make release bundles them
  - N-11 (local): adapters (video, frames, image, burst), synthetic clip, baseline flow detector (synthetic rip 0.98, static clear), VisionPipeline with Detector/SwimmerDetector slots, recheck() per D-03 Rechecker, draw helpers
  - N-12: rw-scheduler and rw-kill-switch with moto tests, stdlib + boto3 import guard
  - N-13: download_ripvis.py (Irikos/RipVIS), make_replay_clips.py, upload_data.py (guarded), local_seed.py (moto only)
  - N-15: ci.yml (python, security, terraform, shell), gated deploy/bench/eval, actions pinned to SHAs; semgrep, pip-audit, actionlint clean
  - Global Claude Code completion chime (Zira voice) in ~/.claude (not in the repo)
- Next step: N-14 bench harness on a branch from main (rw/bench: per-stage latency on the synthetic clip, rw/bench/prices.yaml, bench-local summary pasted here labeled "laptop"); then N-11 step 3 (ingest, camera-sim, rw-vision) once D-03 is on main
- Blockers: D-03 not on main (N-11 ingest); `aws configure --profile ripwatch` missing in WSL (bootstrap plan, network/data plan-local for the DoD); N-01 GitHub settings and docs/01-project-description.md need Noufa; tflint not installed locally (CI runs it)
- Decisions: 11 rows in docs/sprints/sprint-1.md Decision Log dated 2026-10-05 (takeover, docs move, config, metrics, Makefile, Terraform, SSM list, N-09 wiring, N-10, N-11 interface, N-13)
- Requests created: 5 for daksh (see Requests above)
- Commits: db242cb, 1f61758, 5faca2f, 33d51b7, 22aefa7, 4e810e4 (#4); 2f15812, 33c8939, 557b934 (#5); c005a94 (#6); 20149fe (#7); eca74f8 (#8); b788ed0 (#9); 3125db3, fea8c44 (N-11, local); plus this end entry
- Checks at end: unit tests green on every branch (109 on main + CI, 137 with lambdas, 146 with vision, 121 with data scripts); ruff, gitleaks, shellcheck clean
- Pushed: all PR branches yes; noufa/N-11-vision not pushed (WIP until ingest); this end entry pending the push question
- Resume with:
  - git fetch origin && git checkout -b noufa/N-14-bench origin/main
  - uv sync --extra dev --extra cv-std
  - open rw/bench/__main__.py (new) and docs/sprints/sprint-1.md N-14

---

## Session log: Daksh

(no sessions yet)
