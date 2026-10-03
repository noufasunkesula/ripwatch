# RipWatch Handoff

Shared session log for Sprint 1. Rules in `cloud-claude.md` sections 6 to 8. Append only. Never delete another person's lines.

---

## Task board: Noufa

| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| N-01 | Repo scaffold | todo | | |
| N-02 | Tooling | todo | | |
| N-03 | Config and environment | todo | | |
| N-04 | `rw.common` | todo | | |
| N-05 | Makefile | todo | | |
| N-06 | Terraform bootstrap | todo | | |
| N-07 | Terraform modules | todo | | |
| N-08 | Stacks: iam, network, data | todo | | |
| N-09 | Stacks: compute, serverless, edge, observability | todo | | |
| N-10 | Worker runtime | todo | | |
| N-11 | Adapters, ingest, camera-sim, baseline vision | todo | | |
| N-12 | Lambdas: scheduler, kill switch | todo | | |
| N-13 | Data scripts | todo | | |
| N-14 | Bench harness | todo | | |
| N-15 | CI | todo | | |

## Task board: Daksh

| Task | Title | Status | Branch | PR |
|---|---|---|---|---|
| D-01 | Contracts | in-progress | daksh/D-01-contracts | |
| D-02 | Trace and decision helpers | in-progress | daksh/D-02-trace | |
| D-03 | MCP server and data tools | in-progress | daksh/D-03-mcp-tools | |
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
- (from daksh, 2026-10-02) Add `pydantic>=2.7` to the main dependencies in `pyproject.toml` (N-02). Needed by `rw.contracts` and `rw.agent.trace`.
- (from daksh, 2026-10-02) Add a CI step `python -m rw.contracts.export_schemas --check` (N-15). Fails when committed schemas in `docs/contracts/` differ from the models.
- (from daksh, 2026-10-02) Heads up: `rw/__init__.py` already exists on `daksh/D-01-contracts` (one docstring line). Keep either version when N-01 merges.
- (from daksh, 2026-10-02) Join the contract sync with Saif: his request to use original-image pixel coordinates (plus image size) changes what baseline vision emits in N-11.
- (from daksh, 2026-10-03) rw-detections item format for ingest (N-11): write items with `rw.mcp_tools.store.to_item(result)` (or `DynamoDetectionStore.put`). Fields: `camera_id`, `ts_result` = `<start_ts as %Y-%m-%dT%H:%M:%S.%fZ>#<result_id>` (fixed width so string order is time order), `result_id` (needed because a Query cannot filter on key attributes), `expires_at` = created_at + 24 h, `result` = VisionResult JSON string. The table in N-08 needs nothing extra (no GSI).
- (from daksh, 2026-10-03) Dependencies for D-03 in `pyproject.toml` (N-02): `mcp>=2.3,<3` (2.x renamed FastMCP to MCPServer), `boto3`, `moto[server]` (dev), `opencv-python-headless==5.0.0.93` (cv-std). Also ruff `line-length = 100` (existing code uses it).

### For Daksh
- (none)

---

## Session log: Noufa

(no sessions yet)

---

## Session log: Daksh

### Session 1: daksh
- Start: Friday 02 October 2026, 17:13 IST
- End: Friday 02 October 2026, 17:19 IST (6 min)
- Branch: main (no task branch created)
- Tasks: none started
- Done:
  - Orientation: read cloud-claude.md, sprint-1.md, handoff.md, north star
  - Session start routine; found no scaffold on origin (N-01 to N-04 not pushed yet)
- Next step: install uv (needs "yes"), then start D-01 contracts on branch daksh/D-01-contracts (models validate ID prefixes, so not blocked on rw.common.ids)
- Blockers: uv not installed; no pyproject.toml or rw/ package yet (Noufa N-01, N-02); scripts/now.py and scripts/progress.py missing; working on native Windows, not WSL2
- Decisions: none
- Requests created: none
- Commits: none
- Pushed: no
- Resume with:
  - type "daksh", confirm uv install
  - git checkout -b daksh/D-01-contracts origin/main

### Session 2: daksh
- Start: Friday 02 October 2026, 17:22 IST (scripts/now.py missing, time from system clock)
- End: Friday 02 October 2026, 22:54 IST (5 h 32 min)
- Branch: daksh/D-01-contracts, daksh/D-02-trace (branched from D-01)
- Tasks: D-01 (todo -> in-progress), D-02 (todo -> in-progress, code complete)
- Done:
  - Installed uv 0.12.22; dev env at ~/.venvs/ripwatch-dev (pydantic, pytest, ruff) until pyproject.toml lands
  - D-01: 6 contract models in rw/contracts/ with validators, export_schemas.py (+ --check), 6 schemas and README in docs/contracts/, 55 tests
  - D-01: draft shared with Saif; his feedback received (see Decisions)
  - D-02: TraceWriter with step(), rekey() (appends after existing incident steps), 2 KB / 10-item truncation, TraceSink interface + in-memory sink, 16 tests
  - All checks green: 71 tests, ruff, schema check
- Next step: contract sync with Saif (and Noufa): compare Saif's contracts folder with rw/contracts/, decide box format ([x, y, w, h] recommended), original-pixel coords + source_width/source_height, add evidence.frames_detected / frames_window; update models, tests, schemas; record sign-off. Then D-03 (needs demo beach, NOAA station, NWS zone, NWS user agent)
- Blockers: no pyproject.toml / rw.common on origin yet (N-01, N-02, N-04); Saif sign-off pending the merge; repo is on /mnt/c (slow in WSL2, consider ~/code/ripwatch)
- Decisions: 5 rows added to sprint-1.md Decision Log on 2026-10-02 (contract file placement, extra validators, configurable thresholds, Saif's requested changes, TraceSink)
- Requests created: for noufa, pydantic dependency, schema check in CI, rw/__init__.py overlap, join contract sync
- Commits: 42ae7e2 feat(contracts): add v1.0 contract models, schema export and tests (D-01); 387b6be feat(agent): add TraceWriter with rekey and output truncation (D-02)
- Pushed: no
- Resume with:
  - git checkout daksh/D-01-contracts
  - ~/.venvs/ripwatch-dev/bin/python -m pytest tests/unit -q
  - open rw/contracts/vision.py and Saif's contracts folder side by side

### Session 3: daksh
- Start: Saturday 03 October 2026, 15:27 IST (scripts/now.py missing, time from system clock)
- End: Saturday 03 October 2026, 15:27 IST (under 5 min)
- Branch: daksh/D-02-trace
- Tasks: none changed (D-01 in-progress, D-02 in-progress)
- Done:
  - Session start routine only; checks still green (71 tests, ruff, schema check)
  - Found CRLF-only changes in CLAUDE.md, cloud-claude.md, 02-ripwatch-infra-north-star-v1.md (no content change); left uncommitted
- Next step: decide on the CRLF changes (discard recommended), then contract sync with Saif (need his contracts folder), then D-03 MCP server + data tools
- Blockers: no pyproject.toml / rw.common on origin (N-01, N-02, N-04); Saif sign-off pending; D-03 inputs missing: demo beach (proposed Panama City Beach FL, NOAA 8729108, unverified), NWS zone, RW_NWS_USER_AGENT email; `mcp` and `boto3`/`moto` not installed in dev venv
- Decisions: none
- Requests created: none
- Commits: 57b6990 docs(handoff): session 3 end for daksh
- Pushed: yes, after the end entry (origin/daksh/D-01-contracts, origin/daksh/D-02-trace); CRLF question left unanswered, changes left uncommitted
- Resume with:
  - git checkout daksh/D-02-trace
  - git diff --ignore-cr-at-eol --stat (confirm CRLF-only, then decide)
  - ~/.venvs/ripwatch-dev/bin/python -m pytest tests/unit -q

### Session 4: daksh
- Start: Saturday 03 October 2026, 15:29 IST (scripts/now.py missing, time from system clock)
- Progress (as of Saturday 03 October 2026, 17:19 IST, session still open):
  - Branch: daksh/D-03-mcp-tools (from daksh/D-02-trace), pushed to origin
  - D-03 code complete: MCP server `rw/mcp_tools/` (MCPServer, 127.0.0.1:8765, streamable HTTP, /health, heartbeat file, per-call logging with trace_id) and 5 data tools: track_swimmers, predict_spread, get_ocean_conditions, get_flow_stats, zoom_and_recheck
  - DetectionStore (in-memory + DynamoDB) with `to_item()` defining the rw-detections item; OceanSnapshot defines the JSON rw-ocean-poller (D-08) writes to /rw/ocean/latest
  - zoom_and_recheck calls a `Rechecker` interface; VisionPipeline.recheck (N-11) plugs in later, until then the server returns a clear "not available" tool error
  - 151 unit tests passing, ruff clean (line length 100); server smoke-tested over HTTP on 8765
  - Installed into ~/.venvs/ripwatch-dev: mcp 2.3.0, boto3, moto 5.2.3, opencv-python-headless 5.0.0.93
  - Decisions (sprint-1.md Decision Log, 2026-10-03): predict_spread uses seaward_stretch_v1; rw-detections item format; flow trend `unknown`; MCPServer instead of FastMCP (mcp 2.x)
  - Requests created: for noufa, rw-detections item format via `to_item()` (N-11); pyproject deps mcp>=2.3,<3, boto3, moto[server], opencv-python-headless==5.0.0.93, ruff line-length 100 (N-02)
  - Noted: sprint-1.md references sections 9.5 (risk rules, needed by D-05) and 9.6 (SSM params) that do not exist
  - Slip: one smoke-test tool call reached real AWS DynamoDB with dummy credentials (rejected, read-only, nothing changed); future smoke tests use moto
  - Commits: d89b5cb, 3c2f87f, 319544c, 82decc2, c8c15df, b5743fd, 34883bd (all D-03)
  - Next: open PRs for D-01, D-02, D-03 (stacked), then D-04 action tools
