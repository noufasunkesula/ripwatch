# Sprint 1: Write Everything, Plan Everything, Apply Only When a Human Says So

**Sprint dates:** Thu Oct 1 to Wed Oct 7, 2026
**People:** Noufa (infrastructure, platform, worker runtime), Daksh (agent, tools, API)
**Not in this sprint:** Saif (computer vision) works in parallel on the model. Sprint 1 builds the interfaces, the contract, and a working baseline pipeline he plugs into.

---

## Who does what (summary)

### Noufa owns
1. **Repo and tooling:** GitHub repo, folder layout, `pyproject.toml`, linting, pre-commit, `.env.example`, Makefile (N-01, N-02, N-03, N-05)
2. **Shared Python code:** `rw.common` (config, logging, metrics, AWS clients, COOL runtime check, IDs, heartbeat) (N-04)
3. **All Terraform:** bootstrap, 8 modules, 7 stacks, `terraform fmt`, `validate`, `plan` (N-06 to N-09)
4. **Worker runtime:** user data, systemd units, deploy script, SSM deploy document, CloudWatch agent config (N-10)
5. **Input and vision plumbing:** adapters (video, frames, image, burst), camera-sim, ingest, baseline water-motion detector, the `Detector` slot for Saif's model (N-11)
6. **Lambdas:** `rw-scheduler`, `rw-kill-switch` (N-12)
7. **Data scripts:** RipVIS download, replay clip cutting, upload script, local seed (N-13)
8. **Benchmark harness:** `rw.bench` + remote bench script (N-14)
9. **CI:** `ci.yml`, `deploy.yml`, `bench.yml`, `eval.yml` (N-15)
10. **Reviews** every Daksh PR

### Daksh owns
1. **Contracts:** VisionResult, CandidateMessage, AgentDecision, TraceStep, Approval, Job as Pydantic models + JSON Schemas. Gets Saif's sign-off on Day 1 (D-01)
2. **Trace writer:** agent trace steps and rekeying (D-02)
3. **MCP tool server:** 5 data tools (zoom_and_recheck, flow stats, swimmers, predict spread, ocean conditions) (D-03)
4. **MCP action tools:** 6 tools (create incident, watch, alert, request approval, request follow-up capture, close) (D-04)
5. **Agent loop:** Bedrock Converse tool loop, fake model for tests, prompts, cooldown, deterministic risk rules, fallback policy (D-05)
6. **Incident lifecycle:** status transitions, watching and follow-up verification (D-06)
7. **Lambdas:** `rw-api` (dashboard backend, uploads, approvals), `rw-ocean-poller` (NOAA + NWS) (D-07, D-08)
8. **Local integration tests** for the full loop (D-09)
9. **Reviews** every Noufa PR

### Both
- **J-01:** local end-to-end demo on Oct 7
- Sprint review on Oct 7: walk through the Definition of Done together

### Hand-offs between Noufa and Daksh
| From | To | What | By |
|---|---|---|---|
| Daksh | Noufa | Contract models (D-01) so ingest and baseline vision can emit them | Fri Oct 2 |
| Noufa | Daksh | `rw.common` (config, logging, metrics, AWS clients) so tools and agent can use them | Fri Oct 2 |
| Noufa | Daksh | Baseline vision `recheck()` and `draw` helpers for `zoom_and_recheck` and evidence snapshots | Tue Oct 6 |
| Noufa | Daksh | Table, queue, topic names and IAM permissions in Terraform matching what tools and Lambdas need | Mon Oct 5 |
| Daksh | Noufa | List of env vars and IAM actions each Lambda and the agent need | Sun Oct 4 |

**This file is the source of truth for Sprint 1.** If code and this file disagree, this file wins until it is updated. Claude Code reads it through `CLAUDE.md`, ticks tasks off here, and records every decision in the Decision Log at the bottom.

**Parent docs:**
- `docs/01-project-description.md`: what RipWatch is
- `docs/02-infra-north-star.md`: every AWS resource, locked

---

## 0. The one rule of this sprint

> **Write, format, validate and plan freely. Nothing is applied, changed or deleted in AWS unless Noufa or Daksh explicitly says "apply" in the session.**

### 0.1 AWS access for this sprint: `aws configure`

Each of us uses our own IAM user with access keys, set up once with:

```bash
aws configure --profile ripwatch
# AWS Access Key ID:     <your key>
# AWS Secret Access Key: <your secret>
# Default region name:   us-east-1
# Default output format: json
export AWS_PROFILE=ripwatch     # also set in .env
aws sts get-caller-identity     # must show your user, never root
```

| Person | IAM user | Created by | Access |
|---|---|---|---|
| Noufa | `rw-noufa` | Noufa, by hand in the console (the only manual user) | `AdministratorAccess`, MFA on console |
| Daksh | `rw-daksh` | Terraform `iam` stack (after a human-approved apply), or by Noufa by hand if needed earlier | `rw-developers` group, see N-08 |
| Saif | `rw-saif` | Same as Daksh | `rw-developers` group |

Rules for keys:
- Keys live only in `~/.aws/credentials`. Never in the repo, `.env`, tests, logs, chat messages or screenshots. Gitleaks runs on every commit.
- Never use the root account for anything except the one-time Paid plan upgrade and billing.
- Each person creates their own access key in the console (IAM → Users → Security credentials). Terraform never creates access keys, so secrets never land in Terraform state.
- Rotate keys if one is ever pasted anywhere by mistake.

### 0.2 What is allowed

| Always allowed | Allowed only after a human types "apply" (or "destroy") | Never |
|---|---|---|
| Writing any code | `terraform apply` for the named stack, after showing the plan and getting a final "yes" | Root account use |
| `terraform fmt`, `init`, `validate`, `tflint`, `checkov` | `terraform destroy` for the named stack, same rule | Committing keys or `.tfstate` |
| `terraform plan` against our real AWS account | Any mutating `aws` CLI call (create, put, update, delete, run-instances) | Applying more than the stack the human named |
| Read-only `aws` CLI calls (`get`, `list`, `describe`, `sts get-caller-identity`) | Subscribing to COOL, enabling Bedrock model access, uploading data to S3 | `terraform apply -auto-approve` run by Claude Code |
| moto-based tests and local runs | Real Bedrock calls (cost money) | |
| Calling free public APIs (NOAA, NWS) in `network` tests | | |

**How an apply happens:**
1. Human says, for example, "apply bootstrap".
2. Claude Code runs `make plan STACK=bootstrap`, shows a short summary (resources to add, change, destroy, and anything that costs money).
3. Human confirms "yes".
4. Claude Code runs `make apply STACK=bootstrap`, which requires `RW_CONFIRM_APPLY=bootstrap` (the stack name typed again) and runs `terraform apply` on the saved plan file only.
5. Claude Code logs it in `handoff.md` with the date, stack, and resource counts.

### 0.3 Planning order (what can be planned when)

A stack can only be planned once what it reads from already exists. Before anything is applied:

| Stack | Can plan before any apply? | Why |
|---|---|---|
| `bootstrap` | Yes | Uses local state, no dependencies |
| `network` | Only with the local backend override (see N-05 `plan-local`) | Its backend is the state bucket, which bootstrap creates |
| `data` | Same as network | Same |
| `iam` | After bootstrap apply | Needs the state bucket |
| `compute` | After network + data apply, and with `cool_ami_id` set | Reads VPC, subnets, tables, queues |
| `serverless` | After compute + data apply | Reads ASG name, tables, topics |
| `edge` | After serverless apply | Reads HTTP API domain |
| `observability` | After everything above | Reads every name |

`make plan-local STACK=<name>` writes a gitignored `backend_override.tf` (`backend "local" {}`) so a stack can be planned against real AWS before the state bucket exists. Its local state is thrown away; it is only for checking the plan.

Sprint 2 applies everything in the order of north star section 20, using exactly the code written here. Sprint 1 may apply `bootstrap` early if both of us agree, since it is cheap and unlocks planning for the other stacks.

### 0.4 If this feels overwhelming: the lighter path

If by **Sun Oct 4** we are clearly behind, we switch to this path. It keeps every hackathon requirement and both award paths, and drops only polish. Either of us can call it; log the switch in the Decision Log.

| Cut or simplify | Replace with | Saves |
|---|---|---|
| `iam` stack (Terraform-managed users and groups) | Noufa creates `rw-daksh` and `rw-saif` by hand, attached to one hand-made policy | ~half a day |
| 8 Terraform modules | Resources written directly in the stacks; keep only `lambda-fn` and `worker-asg` as modules | ~1 day |
| `observability` stack (3 dashboards, 6 alarms, saved queries) | One dashboard `rw-ops` with agent + benchmark widgets, 2 alarms (DLQ not empty, worker running long) | ~half a day |
| `burst` mode | `video` and `image` only | ~half a day |
| `rw-ocean-poller` Lambda | `get_ocean_conditions` calls NOAA and NWS directly with a 30 min in-memory cache | ~half a day |
| `bench_remote.sh` with Spot instances | Bench `cool` on the worker; run `std-arm` on the same worker in a second venv; run `std-x86` once by hand | ~half a day |
| `rw-camera-sim` as a separate service | Thread inside `rw-ingest` | ~2 hours |
| Separate `rw-vision` unit | Everything vision runs inside `rw-ingest` | ~2 hours |

Never cut: the contract, agent loop with tools, fallback, human approval, traces, COOL runtime check, cost guards (budgets, kill switch, sleep schedule), Terraform for everything we deploy, tests for the agent loop.

---

## 1. Sprint goal and definition of done

**Goal:** on Oct 7 we can run the entire RipWatch loop on a laptop (fake camera clip → ingest → baseline vision → candidate → agent with fake model → MCP tools → incident → approval through the API), every Terraform stack validates and passes security scans, and every stack that can be planned (section 0.3) has a clean `terraform plan` against our real account. Sprint 2 only has to fill in a few IDs and apply.

### Definition of done (all must be true)

- [ ] Repo matches the layout in section 4, `main` branch protected, CI green
- [ ] `make check` passes: ruff, pytest (unit + local integration), terraform fmt + validate on every stack, tflint, checkov (no HIGH or CRITICAL findings, every skip justified inline)
- [ ] `make local-up && make local-demo` runs the full loop on a laptop with moto server and the fake LLM, and prints an incident with a full agent trace and an approval
- [ ] Vision result JSON contract v1 agreed by Saif and Daksh, committed as Pydantic models + generated JSON Schema, validated by tests on both producer and consumer sides
- [ ] All 7 Terraform stacks + bootstrap written, with `terraform.tfvars.example` and documented inputs
- [ ] Noufa and Daksh both have working `aws configure --profile ripwatch` and `aws sts get-caller-identity` shows their IAM user (not root)
- [ ] `terraform plan` is clean (no errors, expected resource counts) for `bootstrap`, and for `network` and `data` via `make plan-local`. Plan outputs summarized in `handoff.md`
- [ ] Nothing applied without a logged human "apply" (`handoff.md` shows who, when, what)
- [ ] Worker runtime written: user data, systemd units, deploy script, SSM deploy document, CloudWatch agent config, runtime check
- [ ] All 4 Lambdas written with unit tests
- [ ] All 11 MCP tools written with unit tests
- [ ] Agent loop written with fake model, fallback policy, cooldown, incident lifecycle, trace writing
- [ ] Data scripts written: RipVIS download, replay clip cutting, upload to S3 (not run against AWS yet)
- [ ] Bench harness written and run locally on one machine (laptop numbers, labeled as such)
- [ ] CI workflows written. `deploy.yml` is guarded and does nothing until Sprint 2
- [ ] `docs/sprint-2-inputs.md` lists every value Sprint 2 still needs, with where to get it
- [ ] Decision Log at the bottom of this file is up to date

---

## 2. Inputs Claude Code must ask for

Claude Code must **never invent** these values. When a task needs one and it is missing, stop, ask the person running the session, suggest a default if one is listed, then write the answer into the right file and log it in the Decision Log.

### 2.1 Where values live

| File | Committed? | Holds |
|---|---|---|
| `.env.example` | Yes | Every variable name with a placeholder and a comment |
| `.env` | **No** (gitignored) | Real values for local scripts and Makefile |
| `infra/**/terraform.tfvars.example` | Yes | Every Terraform input with placeholder |
| `infra/**/terraform.tfvars` | **No** (gitignored) | Real Terraform inputs |
| `infra/backend.hcl` | **No** (gitignored) | Generated by `make backend-config` from `.env` |

**Account ID rule:** Terraform never takes the account ID as a variable. It reads it with `data "aws_caller_identity" "current" {}`. Only the backend config (which cannot use variables) needs it, and `make backend-config` writes it from `AWS_ACCOUNT_ID` in `.env`.

### 2.2 The inputs

| Variable | Needed in Sprint 1? | Ask who | Suggested default / how to find it |
|---|---|---|---|
| `AWS_PROFILE` | Yes | Fixed | `ripwatch` (set with `aws configure --profile ripwatch`, section 0.1). Claude Code never asks for keys and never reads `~/.aws/credentials` |
| `AWS_ACCOUNT_ID` | Yes (backend config, plans) | Noufa | 12 digits. `aws sts get-caller-identity --query Account --output text`. Not a secret, but keep it out of git |
| `AWS_REGION` | Yes | Fixed | `us-east-1`. Do not ask |
| `RW_PREFIX` | Yes | Fixed | `rw`. Do not ask |
| `GITHUB_OWNER` | Yes (OIDC trust policy) | Fixed | `noufasunkesula` |
| `GITHUB_REPO` | Yes | Fixed | `ripwatch` (`https://github.com/noufasunkesula/ripwatch.git`) |
| `RW_TEAM_EMAILS` | Yes (budgets, alarms) | Noufa | Comma-separated team emails |
| `RW_LIFEGUARD_EMAIL` | Yes | Noufa | Suggest a Gmail plus-alias of a team member, e.g. `name+lifeguard@gmail.com`, so demo alerts are easy to filter |
| `RW_BUDGET_USD` | Yes | Fixed | `50` |
| `RW_BEDROCK_BUDGET_USD` | Yes | Fixed | `10` |
| `RW_IAM_USERS` | Yes (iam stack) | Noufa | Usernames for Daksh and Saif (`rw-daksh`, `rw-saif`). Noufa's `rw-noufa` is manual and not in Terraform |
| `RW_COOL_AMI_ID` | **No** (Sprint 2) | Noufa, after subscribing | Leave empty. Compute stack must fail with a clear error message if empty |
| `RW_NOAA_STATION_ID` | Yes (ocean poller tests) | Daksh | Claude Code proposes a US surf beach with a NOAA CO-OPS tide station, shows station name, asks to confirm. Verify at `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json` |
| `RW_NWS_ZONE_ID` | Yes | Daksh | The NWS forecast zone for the same beach. Verify at `https://api.weather.gov/zones?type=forecast&point=<lat>,<lon>` |
| `RW_NWS_USER_AGENT` | Yes | Daksh | NWS requires a contact in the User-Agent. Suggest `RipWatch/0.1 (<team email>)` |
| `RW_BEDROCK_MODEL_ID` | Yes | Fixed | `amazon.nova-lite-v1:0`. Verified with a real call in Sprint 2 |
| `HF_TOKEN` | Only to run the download script | Saif or Noufa | Hugging Face access token. Needed if the RipVIS dataset page asks you to accept terms. Never committed |
| Demo beach coordinates | Yes (camera config) | Daksh | Lat/lon of the beach above. Used for the twin and ocean data |

### 2.3 How Claude Code should give suggestions in real time

While working through this sprint, Claude Code should:
- Before starting a task, check this section for inputs that task needs. Ask for all missing ones in one message, not one at a time.
- When it spots a cheaper, simpler, or safer option than what is written, **say so before writing code**, with the trade-off in one or two lines. Do not silently deviate. If the person agrees, update this file and log the decision.
- When something in this file turns out to be wrong (an API changed, a resource does not support an option), stop, explain, propose a fix, and log it.
- After each task, tick its checkbox and add one line to `handoff.md`.

---

## 3. Fixed technical choices

| Area | Choice |
|---|---|
| Python | 3.12 (matches the COOL venv and Ubuntu 24.04) |
| Packaging | `uv`, one `pyproject.toml`, one `uv.lock` |
| Python package name | `rw` |
| Lint and format | `ruff check`, `ruff format` |
| Tests | `pytest`, `moto[server]` for AWS mocks, `pytest-cov` |
| Data models | Pydantic v2. JSON Schemas generated from the models |
| OpenCV (local, CI, std bench) | `opencv-python-headless==5.0.0.93` in optional group `cv-std` only |
| OpenCV (worker) | COOL's `cv2`, never installed by us. `opencv-python*` is forbidden in the `worker` group |
| AWS SDK | `boto3` (Lambdas use the runtime's boto3, no extra deps) |
| MCP | Official `mcp` Python SDK, FastMCP server, streamable HTTP transport on `127.0.0.1:8765` |
| Metrics | `aws-embedded-metrics` on the worker (agent mode, `tcp://127.0.0.1:25888`). Lambdas print EMF JSON to stdout |
| Logging | stdlib `logging` with a JSON formatter in `rw.common.logging`. No extra logging libraries |
| Terraform | `>= 1.10.0, < 2.0.0`. Providers: `hashicorp/aws ~> 6.0`, `hashicorp/archive ~> 2.0`. Lock files committed |
| Lambda runtime | `python3.12`, `arm64`, stdlib + boto3 only |
| Shell | bash with `set -euo pipefail`, checked with `shellcheck` |
| Local AWS | `moto` in server mode on `http://127.0.0.1:5000`. Not LocalStack |
| Video tools for scripts | `ffmpeg` (local only, for cutting replay clips) |

---

## 4. Repository layout (create exactly this)

```
ripwatch/
  CLAUDE.md                         # written by Noufa from her rules, points here
  README.md
  Makefile
  pyproject.toml
  uv.lock
  .env.example
  .gitignore
  .pre-commit-config.yaml
  .tflint.hcl
  .checkov.yaml
  docs/
    01-project-description.md
    02-infra-north-star.md
    sprint-2-inputs.md
    contracts/
      vision-result.schema.json      # generated, do not hand-edit
      candidate-message.schema.json  # generated
      agent-decision.schema.json     # generated
      README.md                      # how to change a contract
    sprints/
      sprint-1.md                    # this file
  infra/
    backend.hcl.example
    bootstrap/
      main.tf variables.tf outputs.tf versions.tf terraform.tfvars.example
    modules/
      s3-bucket/  dynamodb-table/  sqs-queue/  lambda-fn/
      worker-asg/  cloudfront-site/  cognito/  observability/
    stacks/
      iam/  network/  data/  compute/  serverless/  edge/  observability/
        (each: main.tf variables.tf outputs.tf versions.tf backend.tf terraform.tfvars.example)
  rw/
    __init__.py
    common/        config.py logging.py metrics.py aws.py runtime.py ids.py heartbeat.py
    contracts/     vision.py candidate.py decision.py trace.py export_schemas.py
    adapters/      base.py video.py frames.py image.py burst.py factory.py
    vision/        pipeline.py baseline_flow.py detector.py draw.py state.py
    camera_sim/    __main__.py
    ingest/        __main__.py jobs.py
    mcp_tools/     __main__.py server.py tools/*.py
    agent/         __main__.py loop.py llm.py fake_llm.py fallback.py risk.py prompts.py cooldown.py lifecycle.py trace.py
    bench/         __main__.py stages.py report.py
  lambdas/
    api/           handler.py routes.py auth.py presign.py
    ocean_poller/  handler.py noaa.py nws.py
    scheduler/     handler.py
    kill_switch/   handler.py
  deploy/
    user_data.sh
    rw_deploy.sh
    rw.env.tpl
    cloudwatch-agent.json
    logrotate-rw
    systemd/  rw-camera-sim.service rw-ingest.service rw-vision.service rw-mcp-tools.service rw-agent.service
    ssm/      rw-deploy.yaml
  scripts/
    download_ripvis.py
    make_replay_clips.py
    upload_data.py
    local_seed.py
    local_demo.py
    make_synthetic_clip.py
    bench_remote.sh
  tests/
    unit/  integration/  fixtures/
  ml/
    README.md                        # Saif's area, placeholder only
  .github/workflows/
    ci.yml deploy.yml bench.yml eval.yml
```

**Note on one change from the north star:** vision code lives in `rw/vision/` as part of the one `rw` package (north star listed `rw/` with `vision/` inside, so this matches). Each worker service is a `python -m rw.<service>` entrypoint.

---

## 5. Naming table (every resource name used in code)

Terraform and Python must use these exact names. Python reads them from environment variables with these defaults, never from hard-coded strings scattered in code. `rw/common/config.py` is the only place defaults live.

| Kind | Name | Env var in Python |
|---|---|---|
| State bucket | `rw-tfstate-<account_id>` | n/a |
| Data bucket | `rw-data-<account_id>` | `RW_DATA_BUCKET` |
| Artifacts bucket | `rw-artifacts-<account_id>` | `RW_ARTIFACTS_BUCKET` |
| Frontend bucket | `rw-frontend-<account_id>` | n/a |
| Table | `rw-cameras` | `RW_TABLE_CAMERAS` |
| Table | `rw-jobs` | `RW_TABLE_JOBS` |
| Table | `rw-detections` | `RW_TABLE_DETECTIONS` |
| Table | `rw-incidents` | `RW_TABLE_INCIDENTS` |
| Table | `rw-agent-trace` | `RW_TABLE_TRACE` |
| Table | `rw-approvals` | `RW_TABLE_APPROVALS` |
| Queue | `rw-jobs` (+ `rw-jobs-dlq`) | `RW_QUEUE_JOBS_URL` |
| Queue | `rw-candidates` (+ `rw-candidates-dlq`) | `RW_QUEUE_CANDIDATES_URL` |
| SNS | `rw-lifeguard-alerts` | `RW_TOPIC_LIFEGUARD_ARN` |
| SNS | `rw-ops-alerts` | n/a |
| SNS | `rw-kill-switch` | n/a |
| ASG | `rw-worker-asg` | `RW_WORKER_ASG` (Lambdas) |
| Launch template | `rw-worker-lt` | n/a |
| Security group | `rw-worker-sg` | n/a |
| Instance role | `rw-worker-role` | n/a |
| VPC | `rw-vpc` | n/a |
| SSM document | `rw-deploy` | n/a |
| SSM params | `/rw/...` (section 9.6) | `RW_SSM_PREFIX=/rw` |
| Lambdas | `rw-api`, `rw-ocean-poller`, `rw-scheduler`, `rw-kill-switch` | n/a |
| HTTP API | `rw-http-api` | n/a |
| Cognito pool | `rw-users` | n/a |
| CloudFront | `rw-cdn` | n/a |
| Log groups | `/rw/worker/<service>`, `/aws/lambda/rw-<name>` | n/a |
| Metric namespace | `RipWatch` | `RW_METRIC_NAMESPACE` |
| Dashboards | `rw-ops`, `rw-agent`, `rw-benchmark` | n/a |
| Budgets | `rw-monthly`, `rw-bedrock` | n/a |
| CI role | `rw-gha` | n/a |
| Tags (all resources) | `Project=ripwatch`, `Owner=<name>`, `Component=<stack>`, `ManagedBy=terraform` | n/a |

**Local mode:** `RW_AWS_ENDPOINT_URL=http://127.0.0.1:5000` makes every boto3 client in `rw.common.aws` point to moto server. `RW_LLM=fake` makes the agent use the fake model.

---

## 6. Work split and schedule

### 6.1 Who owns what

| Area | Owner | Reviewer |
|---|---|---|
| Repo, tooling, Makefile, CI | Noufa | Daksh |
| `rw.common` | Noufa | Daksh |
| Contracts (`rw.contracts`) | **Daksh**, signed off by Saif | Noufa |
| Adapters, ingest, camera-sim, baseline vision, bench | Noufa | Daksh (Saif reviews vision interface) |
| All Terraform | Noufa | Daksh |
| Worker runtime (user data, systemd, deploy, CW agent) | Noufa | Daksh |
| MCP tools, agent, risk, lifecycle | Daksh | Noufa |
| Lambdas `rw-api`, `rw-ocean-poller` | Daksh | Noufa |
| Lambdas `rw-scheduler`, `rw-kill-switch` | Noufa | Daksh |
| Local end-to-end demo | Both | Both |

Every change goes through a pull request reviewed by the other person. Small PRs, one task per PR where possible.

### 6.2 Day by day

| Day | Noufa | Daksh |
|---|---|---|
| Thu Oct 1 | N-01 repo scaffold, N-02 tooling, N-03 config/env | D-01 contract draft, share with Saif |
| Fri Oct 2 | N-04 `rw.common`, N-05 Makefile | D-01 contract finalized with Saif, D-02 trace + decision models |
| Sat Oct 3 | N-06 bootstrap, N-07 modules | D-03 MCP server + data tools |
| Sun Oct 4 | N-08 stacks: iam, network, data | D-04 MCP action tools |
| Mon Oct 5 | N-09 stacks: compute, serverless, edge, observability | D-05 agent loop, fake LLM, fallback, cooldown |
| Tue Oct 6 | N-10 worker runtime, N-11 adapters/ingest/camera-sim/baseline vision | D-06 lifecycle + watch, D-07 `rw-api`, D-08 ocean poller |
| Wed Oct 7 | N-12 Lambdas (scheduler, kill switch), N-13 data scripts, N-14 bench, N-15 CI | D-09 local integration tests, J-01 joint local demo, sprint review |

If a day slips, the order inside each column stays the same. The contract (D-01) is the only hard dependency: Noufa's baseline vision must emit it and Daksh's agent must consume it.

---

## 7. Contracts (Daksh owns, Saif signs off)

These are the agreements between vision, agent, API and dashboard. They are Pydantic models in `rw/contracts/`. `python -m rw.contracts.export_schemas` writes the JSON Schemas into `docs/contracts/`. A CI test fails if the committed schemas differ from the generated ones.

**Versioning rule:** every message has `schema_version`. Adding an optional field is a minor bump (`1.1`). Removing or renaming a field is a major bump (`2.0`) and needs both owners to approve.

### 7.1 VisionResult v1.0 (produced by `rw-vision`, one per processed clip or image)

```json
{
  "schema_version": "1.0",
  "result_id": "res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F",
  "trace_id": "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F",
  "camera_id": "cam-01",
  "source_id": "cam-01/20261005T101500Z-000123",
  "job_id": "job_01J9ZC4K9B1C2D3E4F5G6H7J8K",
  "mode": "video",
  "out_of_order": false,
  "input": {
    "s3_uri": "s3://rw-data-<account_id>/incoming/cam-01/20261005T101500Z-000123.mp4",
    "start_ts": "2026-10-05T10:15:00Z",
    "end_ts": "2026-10-05T10:15:10Z",
    "fps_source": 15.0,
    "fps_processed": 5.0,
    "frames_processed": 50,
    "width": 640,
    "height": 360
  },
  "runtime": {
    "variant": "cool",
    "opencv_version": "5.0.0",
    "cv2_path": "/opt/cool/venvs/python_3.12/lib/python3.12/site-packages/cv2/__init__.py",
    "instance_type": "c7g.large",
    "pipeline": "baseline_flow",
    "pipeline_version": "0.1.0"
  },
  "summary": {
    "status": "rip",
    "max_confidence": 0.82,
    "rip_count": 1,
    "swimmer_count": 2,
    "swimmers_at_risk": 1
  },
  "rips": [
    {
      "rip_id": "cam-01-rip-0007",
      "label": "rip",
      "confidence": 0.82,
      "polygon_px": [[312, 140], [340, 138], [355, 260], [300, 262]],
      "bbox_px": [300, 138, 55, 124],
      "polygon_m": null,
      "area_px": 5300,
      "area_m2": null,
      "evidence": {
        "detector_score": null,
        "flow_score": 0.79,
        "seaward_flow_px_per_s": 6.4,
        "seaward_flow_m_per_s": null,
        "timex_score": 0.61
      },
      "first_seen_ts": "2026-10-05T10:14:40Z",
      "persist_s": 30.0
    }
  ],
  "swimmers": [
    {
      "track_id": "cam-01-sw-0012",
      "bbox_px": [330, 200, 12, 18],
      "confidence": 0.71,
      "position_m": null,
      "in_rip_id": "cam-01-rip-0007",
      "distance_to_rip_px": 0.0,
      "distance_to_rip_m": null,
      "drift_px_per_s": [0.4, -1.8]
    }
  ],
  "keyframes": [
    {"index": 0, "ts": "2026-10-05T10:15:00Z", "s3_uri": "s3://rw-artifacts-<account_id>/keyframes/cam-01/res_01J9.../000.jpg"}
  ],
  "quality": {
    "glare": 0.12,
    "blur": 0.05,
    "low_light": false,
    "camera_shake_px": 1.3,
    "notes": []
  },
  "timings_ms": {
    "decode": 210.0, "preprocess": 95.0, "stabilize": 120.0, "timex": 30.0,
    "flow": 640.0, "detect": 0.0, "track": 45.0, "keyframes": 80.0, "total": 1220.0
  },
  "created_at": "2026-10-05T10:15:12Z"
}
```

**Field rules**

| Field | Rule |
|---|---|
| IDs | Prefixed ULIDs: `res_`, `tr_`, `job_`, `inc_`, `dec_`. Generated in `rw.common.ids` |
| `mode` | `video`, `burst`, or `image` |
| `summary.status` | `rip` if `max_confidence >= /rw/vision/rip_threshold` (default 0.70), `uncertain` if `>= /rw/vision/uncertain_threshold` (default 0.40), else `clear` |
| `rips[].label` | `rip` or `uncertain`, same thresholds per rip |
| `polygon_px` | At most 32 points (simplified with `cv2.approxPolyDP`). Coordinates in the processed frame size (`input.width` x `input.height`) |
| `*_m`, `*_m_per_s`, `position_m` | `null` unless the camera has a calibration homography. Never fake metres |
| Motion fields | `null` in `image` mode. Coarse values in `burst` mode |
| `keyframes` | Only written when `status != clear`, or when the camera has an active incident. 1 per second, max 10 |
| `rips` / `swimmers` | Empty lists, never `null` |
| Size | Must serialize under 64 KB (fits a DynamoDB item comfortably) |

### 7.2 CandidateMessage v1.0 (body of each `rw-candidates` SQS message)

```json
{
  "schema_version": "1.0",
  "result_id": "res_...",
  "trace_id": "tr_...",
  "camera_id": "cam-01",
  "mode": "video",
  "status": "rip",
  "max_confidence": 0.82,
  "swimmers_at_risk": 1,
  "active_incident_id": null,
  "reason": "status_rip",
  "created_at": "2026-10-05T10:15:12Z"
}
```

`reason` is one of `status_rip`, `status_uncertain`, `active_incident_followup`. The agent loads the full `VisionResult` from `rw-detections` using `camera_id` + the result's timestamp key (see 9.3), so the message stays small.

**When vision sends a candidate:** if `status` is `rip` or `uncertain`, **or** the camera has an incident in status `watching`, `alerted`, `approved` (so the agent can verify with later clips, including clear ones). Vision refreshes the set of cameras with active incidents every 30 s from the `status-index` GSI.

### 7.3 AgentDecision v1.0 (written by the agent, last step of every trace)

```json
{
  "schema_version": "1.0",
  "decision_id": "dec_...",
  "trace_id": "tr_...",
  "result_id": "res_...",
  "camera_id": "cam-01",
  "incident_id": "inc_...",
  "decision": "alert",
  "risk_level": "HIGH",
  "reasons": ["rip confirmed after zoom (0.82 -> 0.88)", "1 swimmer drifting toward rip"],
  "requested_action": "raise_red_flag",
  "tool_calls": 4,
  "used_fallback": false,
  "model_id": "amazon.nova-lite-v1:0",
  "input_tokens": 2140,
  "output_tokens": 310,
  "latency_ms": 5400,
  "created_at": "2026-10-05T10:15:18Z"
}
```

| Field | Values |
|---|---|
| `decision` | `ignore`, `watch`, `alert`, `resolve`, `close_false_alarm` |
| `risk_level` | `LOW`, `ELEVATED`, `HIGH`, `CRITICAL` |
| `requested_action` | `null`, `raise_red_flag`, `pa_announcement`, `dispatch_lifeguard` |

### 7.4 TraceStep v1.0 (one row per step in `rw-agent-trace`)

```json
{
  "schema_version": "1.0",
  "trace_key": "inc_... or cand_<result_id>",
  "step": 3,
  "trace_id": "tr_...",
  "type": "tool_call",
  "name": "zoom_and_recheck",
  "input": {"result_id": "res_...", "rip_id": "cam-01-rip-0007", "zoom": 2.0},
  "output_summary": {"confidence": 0.88, "label": "rip"},
  "reasoning_summary": "Confidence was 0.82 with glare 0.12, zooming to confirm before alerting.",
  "latency_ms": 640,
  "error": null,
  "created_at": "2026-10-05T10:15:14Z"
}
```

`type` is one of `candidate_received`, `llm_call`, `tool_call`, `decision`, `status_change`, `fallback`, `suppressed_duplicate`, `human_approval`.

**Change from north star (logged):** the trace table partition key is `trace_key`, not `incident_id`, because ignored candidates never create an incident but still need a trace. `trace_key = incident_id` when an incident exists, else `cand_<result_id>`.

### 7.5 Approval v1.0 (row in `rw-approvals`, written by `rw-api`)

```json
{
  "schema_version": "1.0",
  "incident_id": "inc_...",
  "ts": "2026-10-05T10:16:02Z",
  "action": "raise_red_flag",
  "decision": "approve",
  "user_sub": "cognito-sub-uuid",
  "user_email": "lifeguard-demo@...",
  "reason": null,
  "latency_s": 44.0
}
```

`reason` is required when `decision` is `reject`.

### 7.6 Ingest job (row in `rw-jobs`)

```json
{
  "job_id": "job_...",
  "camera_id": "cam-01",
  "source": "camera_sim",
  "s3_key": "incoming/cam-01/20261005T101500Z-000123.mp4",
  "mode": "video",
  "status": "done",
  "result_id": "res_...",
  "error": null,
  "followup_request": null,
  "created_at": "...",
  "updated_at": "..."
}
```

`source`: `camera_sim` or `upload`. `status`: `awaiting_upload`, `queued`, `processing`, `done`, `failed`. `followup_request` is set by the `request_followup_capture` tool and shown in the dashboard.

---

## 8. Noufa's tasks

### N-01 Repo scaffold
- [ ] Use the existing repo `https://github.com/noufasunkesula/ripwatch.git`. Make sure it is private, add Saif and Daksh as collaborators, protect `main`: PR required, 1 approval, CI must pass, no force pushes. (Settings changes on GitHub are done by Noufa in the browser.)
- [ ] Workflow files from `cloud-claude.md` section 9 exist: `CLAUDE.md`, `cloud-claude.md`, `handoff.md`, `scripts/now.py`, `scripts/progress.py`.
- [ ] Create the layout from section 4 with empty `__init__.py` files.
- [ ] Copy `01-project-description.md`, `02-infra-north-star.md`, `sprint-1.md` into `docs/`.
- [ ] `.gitignore` must include: `.env`, `**/terraform.tfvars`, `infra/backend.hcl`, `**/backend_override.tf`, `**/tfplan-*`, `**/.terraform/`, `*.tfstate*`, `.venv/`, `data/`, `work/`, `*.mp4`, `*.zip`, `__pycache__/`, `.pytest_cache/`, `dist/`, `node_modules/`. Must **not** ignore `.terraform.lock.hcl`.

### N-02 Tooling
- [ ] `pyproject.toml` with project `rw`, version `0.1.0`, Python `>=3.12,<3.13`, and dependency groups:
  - base: `boto3`, `pydantic>=2`, `numpy` (version range must be compatible with whatever COOL ships; constrained at install time on the worker), `aws-embedded-metrics`, `mcp`, `python-ulid`
  - optional `cv-std`: `opencv-python-headless==5.0.0.93`
  - optional `dev`: `pytest`, `pytest-cov`, `moto[server]`, `ruff`, `pip-audit`, `huggingface_hub`
  - **No** OpenCV in base or `worker`.
- [ ] `uv lock`, commit `uv.lock`.
- [ ] `ruff` config: line length 100, target `py312`, rules `E,F,I,B,UP,S` (S for security), allow `assert` in tests.
- [ ] `.pre-commit-config.yaml`: ruff, ruff-format, terraform_fmt, gitleaks, shellcheck, end-of-file-fixer.
- [ ] `.tflint.hcl` with the AWS ruleset plugin enabled.
- [ ] `.checkov.yaml`: framework terraform, `soft-fail: false`. Every skip must be an inline `#checkov:skip=<ID>:<reason>` on the resource, never a global skip.

### N-03 Config and environment
- [ ] `.env.example` with every variable from section 2.2 and section 5, each with a comment saying what it is and where to get it.
- [ ] `rw/common/config.py`: a frozen Pydantic `Settings` (or dataclass) loading from environment with the defaults from section 5. One function `get_settings()` cached with `functools.lru_cache`. Raises a clear error naming the missing variable when a required value is absent.
- [ ] `infra/backend.hcl.example` and `make backend-config` that renders `infra/backend.hcl` from `.env` (`bucket = "rw-tfstate-${AWS_ACCOUNT_ID}"`, `region`, `encrypt = true`, `use_lockfile = true`). Each stack's `backend.tf` has only `terraform { backend "s3" { key = "<stack>/terraform.tfstate" } }` and gets the rest via `-backend-config=../../backend.hcl`.
- [ ] `docs/sprint-2-inputs.md`: table of every value still needed for Sprint 2, who provides it, and the exact command or console page to get it.

### N-04 `rw.common`
| File | Must provide |
|---|---|
| `config.py` | From N-03 |
| `logging.py` | `setup_logging(service: str)`: JSON lines with `ts`, `level`, `service`, `msg`, plus any `extra` keys (`trace_id`, `incident_id`, `camera_id`, `result_id`, `job_id`, `mode`). Level from `RW_LOG_LEVEL` (default `INFO`) |
| `metrics.py` | `emit(name, value, unit, dimensions=None)` and a `@timed(stage)` context manager. On the worker uses `aws-embedded-metrics` agent sink. In Lambda prints EMF to stdout. In tests and local mode collects into an in-memory list so tests can assert on metrics. Only metric names from north star 14.2 are allowed; anything else raises in tests |
| `aws.py` | `client(name)` / `resource(name)` factories honoring `RW_AWS_ENDPOINT_URL`, region, adaptive retries (max 3), and read timeout from config. Bedrock client has its own 20 s read timeout |
| `runtime.py` | `check_cv2_runtime()`: logs `cv2.__version__`, `cv2.__file__`, `RW_RUNTIME`. If `RW_RUNTIME=cool`, exits with code 78 when `cv2.__file__` is not under `/opt/cool/` or when any installed distribution name starts with `opencv-python`. Returns a dict used in `VisionResult.runtime` and bench reports |
| `ids.py` | `new_id(prefix)` returning prefixed ULIDs (`res_`, `tr_`, `job_`, `inc_`, `dec_`) |
| `heartbeat.py` | `beat(service)` writes the current epoch to `/var/run/rw/<service>.heartbeat` (path from config so tests use a tmp dir) |

- [ ] Unit tests for every function above. `runtime.py` tested by monkeypatching `cv2.__file__` and installed distributions.

### N-05 Makefile
Every target prints what it does and uses `AWS_PROFILE` from `.env`. Targets marked **READ** only read from AWS (plans, describes, logs) and are always allowed. Targets marked **CHANGE** modify AWS and require `RW_CONFIRM_APPLY=<stack or action name>` to match what is being changed; without it they print the rule from section 0 and exit 1. Claude Code only runs **CHANGE** targets after a human says "apply" (section 0.2).

| Target | Does |
|---|---|
| `help` | Lists targets |
| `setup` | `uv sync --extra dev --extra cv-std`, installs pre-commit hooks |
| `fmt` | ruff format, terraform fmt -recursive |
| `lint` | ruff check, shellcheck on `deploy/` and `scripts/`, tflint on every stack |
| `test` | pytest unit tests |
| `test-int` | starts moto server, runs `tests/integration`, stops it |
| `tf-validate` | for each of bootstrap + 7 stacks: `terraform init -backend=false -input=false` then `terraform validate` (works offline, used by CI) |
| `checkov` | checkov on `infra/` |
| `schemas` | regenerates `docs/contracts/*.json` |
| `check` | fmt check, lint, test, test-int, tf-validate, checkov, schema drift check |
| `backend-config` | renders `infra/backend.hcl` from `.env` |
| `local-up` | starts moto server on :5000, runs `scripts/local_seed.py` |
| `local-demo` | runs `scripts/local_demo.py` (section 10) |
| `local-down` | stops moto server and local services |
| `release` | builds the wheel and `requirements-worker.txt` into `dist/<git_sha>/` (no upload) |
| `whoami` **READ** | `aws sts get-caller-identity`, fails if the caller is root |
| `init STACK=<name>` **READ** | `terraform init -backend-config=../../backend.hcl` (bootstrap: local state) |
| `plan STACK=<name>` **READ** | `terraform plan -out=tfplan-<name>` and prints a summary: add / change / destroy counts and a list of resources that cost money |
| `plan-local STACK=<name>` **READ** | Writes gitignored `backend_override.tf` with `backend "local" {}`, runs `init -reconfigure` and `plan`, then removes the override. For stacks whose state bucket does not exist yet |
| `plan-all` **READ** | Plans every stack that section 0.3 says is plannable right now, skips the rest with a reason |
| `apply STACK=<name>` **CHANGE** | Requires `RW_CONFIRM_APPLY=<name>`. Applies only the saved `tfplan-<name>` (fails if it is missing or older than 30 minutes) |
| `destroy STACK=<name>` **CHANGE** | Requires `RW_CONFIRM_APPLY=destroy-<name>`. Refuses `bootstrap` and `data` unless `RW_CONFIRM_APPLY=destroy-<name>-really` |
| `up` **CHANGE** | Plans and applies stacks in order (iam, network, data, compute, serverless, edge, observability), stopping for confirmation before each apply. Requires `RW_CONFIRM_APPLY=up` |
| `down` **CHANGE** | Destroys stacks in reverse order, never bootstrap or data bucket contents. Requires `RW_CONFIRM_APPLY=down` |
| `wake` / `sleep` **CHANGE** | Invoke `rw-scheduler` with `{"action":"wake"}` / `{"action":"sleep"}`. Requires `RW_CONFIRM_APPLY=wake` / `sleep` |
| `deploy` **CHANGE** | Upload release, set `/rw/release`, run SSM `rw-deploy`. Requires `RW_CONFIRM_APPLY=deploy` |
| `ssm` **READ** | Opens an SSM session to the worker |
| `logs` **READ** | Tails `/rw/worker/*` with `aws logs tail` |
| `bench` **CHANGE** | Runs `scripts/bench_remote.sh`. Requires `RW_CONFIRM_APPLY=bench` |
| `bench-local` | runs `python -m rw.bench` on this machine, writes `work/bench/<run_id>/` |

### N-06 Terraform bootstrap (`infra/bootstrap/`)
Uses **local state** (it creates the state bucket). After the Sprint 2 apply, its state is migrated into the bucket with `terraform init -migrate-state`; document this in the stack README.

| Resource | Spec |
|---|---|
| `aws_s3_bucket` state | `rw-tfstate-<account_id>`, `lifecycle { prevent_destroy = true }`, versioning enabled, SSE-S3, all 4 public access blocks true, noncurrent version expiration 90 days, bucket policy denying `aws:SecureTransport = false` and denying `s3:DeleteObject` on `*.tfstate` for all principals except the account root |
| `aws_iam_openid_connect_provider` | `https://token.actions.githubusercontent.com`, client id `sts.amazonaws.com` |
| `aws_iam_role` `rw-gha` | Trust: the OIDC provider, `aud = sts.amazonaws.com`, `sub` matching `repo:<owner>/<repo>:ref:refs/heads/main` and `repo:<owner>/<repo>:pull_request`. Permissions: start with `PowerUserAccess` plus scoped IAM permissions limited to `arn:aws:iam::*:role/rw-*`, `policy/rw-*`, `instance-profile/rw-*`. Leave a `TODO(sprint-3)` comment to narrow it |
| `aws_sns_topic` `rw-ops-alerts` | Email subscriptions for `RW_TEAM_EMAILS`. Topic policy allows `budgets.amazonaws.com` and `cloudwatch.amazonaws.com` to publish |
| `aws_sns_topic` `rw-kill-switch` | No email subscribers. Policy allows `budgets.amazonaws.com` to publish. The `rw-kill-switch` Lambda subscribes in the serverless stack |
| `aws_budgets_budget` `rw-monthly` | Cost, monthly, `RW_BUDGET_USD`. Notifications: ACTUAL 25%, 50%, 100% to team emails + `rw-ops-alerts`. ACTUAL 80% to team emails + `rw-kill-switch` |
| `aws_budgets_budget` `rw-bedrock` | Cost filter on service "Amazon Bedrock", `RW_BEDROCK_BUDGET_USD`, ACTUAL 80% and 100% to team emails |
| `aws_ce_anomaly_monitor` + `aws_ce_anomaly_subscription` | Service monitor, daily email to team, threshold $5 absolute impact |

Outputs: state bucket name, OIDC role ARN, ops topic ARN, kill-switch topic ARN.

### N-07 Terraform modules (`infra/modules/`)
Every module: `variables.tf` with descriptions and types, `outputs.tf`, a short `README.md`, tags passed through a `tags` variable merged with defaults.

| Module | Inputs (main) | Creates | Outputs |
|---|---|---|---|
| `s3-bucket` | `name`, `versioning`, `lifecycle_rules`, `force_destroy` (default false), `cors_rules` | Bucket, public access block (all true), SSE-S3, ownership controls `BucketOwnerEnforced`, TLS-only policy merged with an optional extra policy JSON | `id`, `arn`, `bucket_regional_domain_name` |
| `dynamodb-table` | `name`, `hash_key`, `range_key`, `attributes`, `ttl_attribute`, `gsis`, `pitr` (default false) | On-demand table, SSE enabled | `name`, `arn`, `stream_arn` (null) |
| `sqs-queue` | `name`, `visibility_timeout_s`, `max_receive_count` (default 3), `retention_s` | Queue + DLQ (`<name>-dlq`, 14 day retention), redrive policy, SSE-SQS | `url`, `arn`, `dlq_url`, `dlq_arn`, `name` |
| `lambda-fn` | `name`, `source_dir`, `handler`, `env`, `policy_json`, `timeout`, `memory` (default 256), `log_retention_days` (default 7) | `archive_file` zip, role with basic execution + `policy_json`, function (`python3.12`, `arm64`), log group created before the function | `arn`, `name`, `invoke_arn`, `role_arn` |
| `worker-asg` | `ami_id` (validated non-empty with a clear error), `instance_type` (default `c7g.large`), `subnet_ids`, `security_group_id`, `instance_profile_name`, `user_data`, `volume_gb` (default 30) | Launch template `rw-worker-lt` (IMDSv2 required, hop limit 1, gp3 encrypted, public IP, tag `rw:role=worker`), ASG `rw-worker-asg` (min 0, max 1, desired 0, both subnets, `ignore_changes = [desired_capacity]` so wake/sleep never fights Terraform) | `asg_name`, `launch_template_id` |
| `cloudfront-site` | `name`, `bucket` info, `api_origin_domain`, `price_class` (default `PriceClass_100`) | Distribution with OAC to S3 (default behavior, `CachingOptimized`), ordered behavior `/api/*` to the HTTP API (`CachingDisabled`, origin request policy `AllViewerExceptHostHeader`, all methods), SPA fallback (403/404 → `/index.html`), `viewer_protocol_policy = redirect-to-https`, min TLS 1.2 | `domain_name`, `distribution_id`, `arn` |
| `cognito` | `name`, `callback_urls`, `logout_urls` | User pool (email sign-in, admin-create-user only, password policy min 12, MFA optional TOTP), app client without secret (SPA), hosted UI domain `rw-<random suffix>` | `user_pool_id`, `client_id`, `issuer_url`, `domain` |
| `observability` | names of queues, ASG, Lambdas, SNS topic ARN | 3 dashboards, alarms from north star 14.4, 3 saved Logs Insights queries | dashboard names |

### N-08 Stacks: iam, network, data

**`stacks/iam`**
- Noufa's `rw-noufa` admin user is created by hand and is **not** managed here (it is the user that runs Terraform).
- `aws_iam_group` `rw-developers` with the policy below, `aws_iam_user` for each name in `RW_IAM_USERS` (`rw-daksh`, `rw-saif`), group membership, `force_destroy = false`.
- `aws_iam_user_login_profile` is **not** used (it would put a password in state). Noufa sets console passwords by hand, users must change them on first login and enable MFA.
- No `aws_iam_access_key` resources. Each person creates their own access key in the console.
- Group policy `rw-require-mfa-console`: denies everything except managing your own MFA and password when the request is from the console without MFA (`aws:MultiFactorAuthPresent = false` and `aws:ViaAWSService = false`, scoped so CLI access-key calls still work).
- `rw-developers` policy: read `rw-data-*`; read/write `rw-artifacts-*`; read CloudWatch logs, metrics, dashboards; `bedrock:InvokeModel*` and `bedrock:Converse*`; `ssm:StartSession` only on instances tagged `rw:role=worker`; read SSM params under `/rw/`; read DynamoDB `rw-*`; explicit deny `s3:DeleteObject` on `rw-data-*/raw/*` and on `rw-tfstate-*`.

**`stacks/network`**
- `rw-vpc` `10.20.0.0/16`, DNS hostnames and support on.
- `rw-public-a` `10.20.1.0/24` in `us-east-1a`, `rw-public-b` `10.20.2.0/24` in `us-east-1b`, `map_public_ip_on_launch = true`.
- IGW, one public route table, associations.
- Gateway endpoints for S3 and DynamoDB attached to the route table.
- `rw-worker-sg`: no ingress. Egress TCP 443 to `0.0.0.0/0` only. Add egress UDP 123 to `169.254.169.123/32` for time sync. Checkov will flag egress to the world; skip inline with the reason "worker must reach Bedrock, NOAA, NWS, PyPI over HTTPS; no NAT by design (north star section 6)".
- Default security group of the VPC restricted to no rules.
- Outputs: VPC id, subnet ids, SG id, route table id.

**`stacks/data`**
- Buckets via `s3-bucket` module:
  - `rw-data-<account_id>`: versioning off, lifecycle expire `incoming/` after 7 days, CORS allowing `POST`/`PUT` from the CloudFront domain (variable, placeholder until edge exists; document the two-pass apply in Sprint 2).
  - `rw-artifacts-<account_id>`: versioning on, noncurrent expiry 14 days, expire `keyframes/` after 7 days, expire `cloudtrail/` after 30 days, bucket policy allowing CloudTrail writes to `cloudtrail/` and Bedrock logging writes to `bedrock-logs/`.
  - `rw-frontend-<account_id>`: versioning off. Bucket policy for CloudFront OAC is added in the edge stack.
- DynamoDB via module:

| Table | Hash | Range | TTL | GSI |
|---|---|---|---|---|
| `rw-cameras` | `camera_id` (S) | none | none | none |
| `rw-jobs` | `job_id` (S) | none | `expires_at` | `camera-index`: `camera_id` + `created_at` |
| `rw-detections` | `camera_id` (S) | `ts_result` (S, `<iso_ts>#<result_id>`) | `expires_at` | none |
| `rw-incidents` | `incident_id` (S) | none | none | `status-index`: `status` + `created_at` |
| `rw-agent-trace` | `trace_key` (S) | `step` (N) | none | none |
| `rw-approvals` | `incident_id` (S) | `ts` (S) | none | none |

  (Note: `rw-detections` keys changed from north star `source_id + ts` to `camera_id + ts_result` so "latest results for a camera" is one query. Logged.)
- SQS via module: `rw-jobs` (visibility 300 s), `rw-candidates` (visibility 120 s). Queue policy on `rw-jobs` allowing `s3.amazonaws.com` from the data bucket ARN.
- S3 event notification on `rw-data`: `s3:ObjectCreated:*` to `rw-jobs`, one entry per suffix `.mp4`, `.mov`, `.jpg`, `.jpeg`, `.png`, `.zip`, each with prefix `incoming/`.
- SSM parameters from section 9.6 with the listed defaults. `/rw/release` starts as `none`. `lifecycle { ignore_changes = [value] }` on parameters that CI or Lambdas change (`/rw/release`, `/rw/ocean/latest`).
- CloudTrail `rw-trail`: management events, single region, log file validation on, to `rw-artifacts/cloudtrail/`.
- `aws_bedrock_model_invocation_logging_configuration`: text delivery to `rw-artifacts` prefix `bedrock-logs/`, no image or embedding delivery.

### N-09 Stacks: compute, serverless, edge, observability

**`stacks/compute`**
- `rw-worker-role` + instance profile. Policies: `AmazonSSMManagedInstanceCore`, `CloudWatchAgentServerPolicy`, plus inline least-privilege policy: `s3:GetObject`/`ListBucket` on `rw-data`, `s3:CopyObject` from `replay/` to `incoming/` (expressed as `GetObject` on `replay/*` + `PutObject` on `incoming/*`), `s3:PutObject`/`GetObject` on `rw-artifacts` prefixes `evidence/`, `keyframes/`, `benchmarks/`, `eval/`, `traces/`, `results/`, `GetObject` on `releases/` and `models/`; DynamoDB CRUD on the 6 tables + their indexes; SQS receive/delete/change visibility on `rw-jobs`, send/receive/delete on `rw-candidates`; `sns:Publish` on `rw-lifeguard-alerts`; `bedrock:InvokeModel` + `bedrock:Converse` on the configured model ARN; `ssm:GetParameter(s)ByPath` on `/rw/*`; `ssm:PutParameter` not allowed.
- `worker-asg` module with `ami_id = var.cool_ami_id` and user data rendered from `deploy/user_data.sh` via `templatefile` (inputs: artifacts bucket, region, runtime `cool`, python path).
- Log groups `/rw/worker/rw-camera-sim`, `rw-ingest`, `rw-vision`, `rw-mcp-tools`, `rw-agent`, `deploy`, 7-day retention.
- SSM document `rw-deploy` from `deploy/ssm/rw-deploy.yaml` (Command type, `aws:runShellScript`, parameter `release`, runs `/opt/rw/bin/rw_deploy.sh {{ release }}`, timeout 900 s).
- SSM parameter `/rw/cloudwatch-agent/config` containing `deploy/cloudwatch-agent.json`.

**`stacks/serverless`**
- SNS `rw-lifeguard-alerts` with email subscription `RW_LIFEGUARD_EMAIL`.
- Cognito via module.
- Lambdas via module:

| Lambda | Timeout | Env | Permissions |
|---|---|---|---|
| `rw-api` | 15 s | table names, buckets, topic ARN, `RW_ALLOWED_ORIGIN` | DynamoDB read on all tables, write on `rw-approvals`, `rw-incidents`, `rw-jobs`, `rw-cameras`; S3 presign PUT/POST on `rw-data/incoming/*`, presign GET on `rw-data/replay/*`, `rw-data/incoming/*`, `rw-artifacts/evidence/*`, `rw-artifacts/keyframes/*`; `sns:Publish` lifeguard topic |
| `rw-ocean-poller` | 30 s | station, zone, user agent | `ssm:PutParameter` and `GetParameter` on `/rw/ocean/latest` only |
| `rw-scheduler` | 120 s | ASG name, table names, artifacts bucket | `autoscaling:SetDesiredCapacity` + `UpdateAutoScalingGroup` on `rw-worker-asg`, `autoscaling:DescribeAutoScalingGroups`, DynamoDB scan on incidents/trace/approvals, `s3:PutObject` on `rw-artifacts/traces/*` |
| `rw-kill-switch` | 30 s | ASG name, ops topic ARN | same autoscaling permissions, `sns:Publish` on `rw-ops-alerts` |

- SNS subscription `rw-kill-switch` topic → `rw-kill-switch` Lambda, plus Lambda permission for SNS.
- EventBridge Scheduler (`aws_scheduler_schedule`) with timezone `Asia/Kolkata`: `rw-sleep-nightly` at `cron(0 1 * * ? *)` → `rw-scheduler` `{"action":"sleep"}`; `rw-export-nightly` at `cron(15 1 * * ? *)` → `{"action":"export"}`; `rw-ocean-poll` `rate(30 minutes)` → `rw-ocean-poller`. A variable `judging_mode` (default false); when true, `rw-sleep-nightly` is disabled.
- API Gateway HTTP API `rw-http-api`: JWT authorizer (issuer = Cognito issuer URL, audience = client id), Lambda proxy integration (payload v2), routes from section 9.4 (all with the authorizer except `GET /api/health`), `$default` stage with auto-deploy, throttling 20 rps / burst 50, access logs to `/aws/apigateway/rw-http-api` with 7-day retention.

**`stacks/edge`**
- `cloudfront-site` module with the frontend bucket and the HTTP API domain.
- Frontend bucket policy for the distribution (OAC, `AWS:SourceArn` condition).
- Output the CloudFront domain. Document that Cognito callback URLs and the data bucket CORS origin are updated with this domain in a second apply (Sprint 2).
- Placeholder `frontend/` not built in Sprint 1; deploy step uploads a static `index.html` that says "RipWatch dashboard coming in Sprint 3" so the stack is testable.

**`stacks/observability`**
- `observability` module wired to real names.
- Dashboards (JSON built with `jsonencode`, not hand-written strings):
  - `rw-ops`: `FramesPerSecond`, `FrameLatencyMs` by `stage`, `ApproximateNumberOfMessagesVisible` for both queues and both DLQs, `JobsProcessed` by `mode`, `rw-api` errors, worker `CPUUtilization` (free EC2 metric, by ASG).
  - `rw-agent`: `RipCandidates`, `FalseAlarmsRejected`, `IncidentsCreated`, `AgentToolCalls`, `AgentDecisionLatencyMs`, `AgentFallbackUsed`, `BedrockTokens`, `ApprovalLatencySec`.
  - `rw-benchmark`: `FrameLatencyMs` by `runtime` and `stage`, `CostPerCameraHour` by `runtime`.
- Alarms (all to `rw-ops-alerts`): `rw-fps-low` (`FramesPerSecond` < 4 for 5 x 1 min, treat missing as not breaching), `rw-jobs-dlq-not-empty`, `rw-candidates-dlq-not-empty`, `rw-agent-fallback-high` (metric math: fallback / decisions > 0.2 over 15 min), `rw-api-errors` (Lambda `Errors` > 5 in 5 min), `rw-worker-running-long` (ASG `GroupInServiceInstances` >= 1 for 14 consecutive 1 h periods; disabled when `judging_mode = true`).
- Saved queries: `rw-errors`, `rw-slow-frames`, `rw-incident-timeline` (filter by `trace_id`).

**Acceptance for N-06 to N-09:** `make tf-validate` and `make checkov` pass, every stack has `terraform.tfvars.example`, every stack README lists inputs, outputs, and apply order dependencies.

### N-10 Worker runtime (`deploy/`)

**`deploy/user_data.sh`** (templated by Terraform, idempotent, logs to `/var/log/rw/user-data.log`):
1. `set -euo pipefail`, create user `rw` (no login shell), dirs `/opt/rw`, `/etc/rw`, `/var/log/rw`, `/var/lib/rw/work`, `/var/run/rw`.
2. Ensure SSM agent: if `snap list amazon-ssm-agent` fails, `snap install amazon-ssm-agent --classic`; enable and start.
3. Install CloudWatch agent from `https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb` (arch from `dpkg --print-architecture`), start it with config from SSM `/rw/cloudwatch-agent/config`.
4. Write `/etc/rw/rw.env` from `deploy/rw.env.tpl` (bucket names, table names, queue URLs looked up by name with the AWS CLI, region, `RW_RUNTIME`, `RW_PYTHON`).
5. Python environment:
   - `RW_RUNTIME=cool`: `RW_PYTHON=/opt/cool/venvs/python_3.12/bin/python`. Generate `/opt/rw/cool-constraints.txt` from `$RW_PYTHON -m pip freeze` filtered to `numpy` (pins numpy to COOL's version).
   - `RW_RUNTIME=std-arm` or `std-x86`: create `/opt/rw/venv` with `python3.12 -m venv`, `RW_PYTHON=/opt/rw/venv/bin/python`, install `opencv-python-headless==5.0.0.93`.
6. Install systemd units from the release, `logrotate` config, then call `rw_deploy.sh "$(aws ssm get-parameter --name /rw/release ...)"` unless the value is `none`.

**`deploy/rw_deploy.sh <release_sha>`**:
1. Download `s3://rw-artifacts/releases/<sha>/rw-0.1.0-py3-none-any.whl` and `requirements-worker.txt` to `/opt/rw/releases/<sha>/`.
2. `$RW_PYTHON -m pip install --no-deps` of the wheel, then `-r requirements-worker.txt -c /opt/rw/cool-constraints.txt -c deploy/constraints.txt` where `deploy/constraints.txt` blocks OpenCV wheels (pip constraints cannot forbid a package, so the script also fails if `pip list` shows any `opencv-python*` after install on `cool`).
3. `$RW_PYTHON -c "from rw.common.runtime import check_cv2_runtime; check_cv2_runtime()"` must exit 0.
4. Copy systemd units, `systemctl daemon-reload`, restart all `rw-*` units.
5. Health check for 60 s: every unit `is-active`, every heartbeat file newer than 30 s. On failure, roll back to the previous release in `/etc/rw/release.previous` and exit non-zero.
6. Write `/etc/rw/release`, log one JSON line with release, runtime, cv2 version to `/var/log/rw/deploy.log`.

**`deploy/systemd/rw-<service>.service`** (5 units):
- `User=rw`, `EnvironmentFile=/etc/rw/rw.env`, `ExecStart=${RW_PYTHON} -m rw.<module>` (use a wrapper `/opt/rw/bin/run <module>` because systemd does not expand variables in the executable path), `Restart=always`, `RestartSec=5`, `MemoryMax` per north star 8.2 (`rw-camera-sim` 128M, `rw-ingest` 512M, `rw-vision` 2G, `rw-mcp-tools` 512M, `rw-agent` 512M), `StandardOutput=append:/var/log/rw/<service>.log`, `StandardError=inherit`, `NoNewPrivileges=true`, `ProtectSystem=full`, `PrivateTmp=true`.
- `rw-agent` has `After=rw-mcp-tools.service` and `Requires=rw-mcp-tools.service`.

**`deploy/cloudwatch-agent.json`**: logs from `/var/log/rw/<service>.log` to `/rw/worker/<service>` (stream `{instance_id}`), `/var/log/rw/deploy.log` and `user-data.log` to `/rw/worker/deploy`, `"metrics_collected": {"emf": {}}` under `logs` so EMF on port 25888 works. No host metrics (EC2 basic metrics are free, agent metrics are billed).

**`deploy/logrotate-rw`**: daily, keep 3, compress, `copytruncate`.

- [ ] `shellcheck` clean. A test in `tests/unit/test_deploy_files.py` renders the user data template with dummy values and asserts it contains no unrendered `${...}` Terraform placeholders, and checks every unit file has the required keys.

### N-11 Adapters, ingest, camera-sim, baseline vision

**Adapters (`rw/adapters/`)**: `Frame` dataclass (`image: np.ndarray`, `ts: datetime`, `index: int`, `source_id: str`, `camera_id: str`). `FrameSource` protocol with `mode` and `iter_frames(target_fps, max_width)`.

| Class | Rules |
|---|---|
| `VideoFileSource` | `cv2.VideoCapture`. Reads source fps (fallback 15 if 0). Keeps every Nth frame to hit `target_fps` (default 5). Resizes to `max_width` (default 640) keeping aspect with `INTER_AREA`. Timestamps from clip start (from object key) + frame time |
| `FrameFolderSource` | Sorted `.jpg`/`.png`, assumes `RW_FRAME_FOLDER_FPS` (default 15) for timestamps |
| `ImageSource` | One frame, mode `image` |
| `BurstSource` | `.zip` of 2 to 10 images, sorted by name, mode `burst`, timestamps 1 s apart unless EXIF time exists |
| `factory.source_for(path)` | Picks by extension: `.mp4`/`.mov` video, `.jpg`/`.jpeg`/`.png` image, `.zip` burst. Anything else raises `UnsupportedInput` |

**`rw/camera_sim`**:
- Every `RW_CAMERA_SIM_INTERVAL_S` (default 10): for each enabled camera in `rw-cameras` (field `replay_prefix`), copy the next object from `replay/clips/<camera_id>/` to `incoming/<camera_id>/<UTC yyyymmddThhmmssZ>-<seq:06d>.mp4` with metadata `source-clip`. Loops at the end. Pointer kept in `/var/lib/rw/camera_sim_state.json`. Heartbeat every loop.
- Writes a `rw-jobs` row (`source=camera_sim`, `status=queued`) before copying, so jobs are visible even before ingest picks them up.

**`rw/ingest`**:
- Long-polls `rw-jobs` queue (wait 20 s, max 1 message, visibility 300 s). Ignores S3 test events.
- Parses key → `camera_id`, timestamp, seq, `job_id` (uploads use `incoming/<camera_id>/<job_id>.<ext>`; camera-sim uses the timestamp-seq pattern and looks up its job row).
- Downloads to `/var/lib/rw/work/<job_id>/`, picks adapter, updates job `processing`, calls `VisionService.process(source, job)`, updates job `done` with `result_id` or `failed` with error, deletes the message, deletes the work dir.
- **Per-camera ordering:** keeps `last_seq[camera_id]`. If a camera-sim clip arrives with a lower seq than already processed, it is processed with a fresh state and `out_of_order=true`, and per-camera state is not updated.
- **Per-camera state** (`rw/vision/state.py`): last 2 processed frames (grayscale), rolling timex accumulator (last 60 s), flow history (last 6 clips of mean flow fields at 1/4 resolution), swimmer tracks, active rip ids. Held in memory; lost on restart (acceptable, documented).
- Ingest and vision run in **separate systemd units** per north star, but to avoid passing frames between processes, `rw-ingest` imports and calls the vision pipeline in-process. `rw-vision` unit runs the **candidate sender and active-incident refresher** plus a health endpoint-free heartbeat. Document this split in the code docstring. (Decision logged: it keeps frames in one process and memory.)

**Vision interface (`rw/vision/pipeline.py`)** for Saif to implement against:

```python
class Detector(Protocol):
    name: str
    version: str
    def detect(self, frames: list[np.ndarray]) -> list[RipDetection]: ...

class SwimmerDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[SwimmerBox]: ...

class VisionPipeline:
    def __init__(self, detector: Detector | None, swimmer_detector: SwimmerDetector | None, settings: Settings): ...
    def process(self, frames: Iterable[Frame], mode: Mode, state: CameraState) -> VisionResult: ...
    def recheck(self, crops: list[np.ndarray], mode: Mode) -> RecheckResult: ...
```

**Baseline pipeline (`rw/vision/baseline_flow.py`)**: a real, classic-OpenCV flow-only rip detector. It is our baseline for evaluation and the default until Saif's model lands. Stages (each wrapped in `metrics.timed(stage)`):
1. `preprocess`: `cv2.resize` (already done by adapter), `cv2.cvtColor` to gray, `cv2.GaussianBlur` 5x5, CLAHE on the L channel for glare.
2. `stabilize`: `cv2.goodFeaturesToTrack` + `cv2.calcOpticalFlowPyrLK` against the previous frame, `cv2.estimateAffinePartial2D`, `cv2.warpAffine`. Record mean shift in `quality.camera_shake_px`.
3. `timex`: running mean with `cv2.accumulateWeighted`; `timex_score` from darkness of candidate region relative to the surf band.
4. `flow`: `cv2.calcOpticalFlowFarneback` on frames downscaled to 1/2 (configurable), averaged over the clip and over the flow history.
5. Seaward direction: from camera config `seaward_vector` (unit vector in image coords, default `[0, -1]` meaning "up in the image is the sea"). Project mean flow onto it.
6. `detect`: threshold the seaward component, `cv2.morphologyEx` open/close, `cv2.findContours`, `cv2.approxPolyDP`, keep regions above `/rw/vision/min_rip_area_px`. Confidence = logistic of (normalized seaward magnitude, persistence across clips, timex score).
7. `track`: if a `SwimmerDetector` is configured, run it on 1 frame per second and associate with IoU tracking. If not, swimmers list is empty.
8. `keyframes`: write JPEGs (quality 85) to `keyframes/<camera_id>/<result_id>/<idx>.jpg` when required by contract rules.
9. In `image` mode: skip 2 to 6, run the detector only (baseline returns `uncertain` with low confidence from a simple appearance heuristic: dark low-texture gap inside the bright surf band using `cv2.Laplacian` variance), so image mode always produces a valid result.
10. In `burst` mode: align with ORB + `cv2.findHomography` (RANSAC), then run flow between consecutive aligned images with `fps_processed = 1`.

**`rw/vision/__main__.py`** (unit `rw-vision`): refreshes active-incident cameras every 30 s, exposes them to ingest through a small file `/var/run/rw/active_cameras.json` (atomic write), heartbeats. Documented as the place where Saif's detector loading and model download (`rw-artifacts/models/<name>/<version>/model.onnx` to `/var/lib/rw/models/`) will live.

**Results:** after `process`, ingest writes the full `VisionResult` to `rw-detections` (`ts_result = <input.start_ts>#<result_id>`, `expires_at` = now + 24 h), emits metrics (`FramesPerSecond`, `FrameLatencyMs` per stage, `RipCandidates` by mode, `JobsProcessed` by mode), and sends a `CandidateMessage` when the rules in 7.2 say so.

- [ ] Tests: `scripts/make_synthetic_clip.py` generates a 10 s synthetic video (moving sinusoidal "waves" plus a darker strip with upward motion) used as a fixture. Baseline must return `status=rip` on it and `clear` on a static clip. Adapter tests for each input type. Ordering test for out-of-order seqs.

### N-12 Lambdas: scheduler and kill switch
- `rw-scheduler` handler accepts `{"action": "wake" | "sleep" | "export" | "status"}`. `wake` sets desired 1, `sleep` sets desired 0, `status` returns ASG instances and lifecycle states, `export` scans `rw-incidents`, `rw-agent-trace`, `rw-approvals` and writes JSON Lines to `rw-artifacts/traces/<YYYY-MM-DD>/<table>.jsonl`. Emits a log line per action.
- `rw-kill-switch` handler: on any SNS message, sets desired 0, publishes to `rw-ops-alerts` "Kill switch fired: worker scaled to 0. Budget message: <first 500 chars>". Idempotent.
- [ ] Unit tests with moto for both, including export with sample rows.

### N-13 Data scripts
| Script | Does | Notes |
|---|---|---|
| `scripts/download_ripvis.py` | Uses `huggingface_hub.snapshot_download` with `allow_patterns` to fetch: `train/sampled_images.zip`, `train/yolo_annotations.zip`, `train/train.json`, `val/*` (videos, frames, labels), `test/*.mp4`. Writes to `data/ripvis/`. Flags `--dry-run` (lists files and total size only) and `--parts train,val,test` | Reads `HF_TOKEN` from env. Prints the license line and reminds that data must never be committed or made public |
| `scripts/make_replay_clips.py` | For each chosen test video: `ffmpeg -i in.mp4 -vf scale=960:-2 -r 15 -an -c:v libx264 -crf 23 -f segment -segment_time 10 -reset_timestamps 1 out_%04d.mp4`. Writes `data/replay/clips/<camera_id>/`. Config file `scripts/replay_cameras.yaml` maps cameras to source videos (default: `cam-01` and `cam-02` using 3 test videos each with visible rips, chosen by Saif) | ffmpeg must be installed locally |
| `scripts/upload_data.py` | Syncs `data/ripvis/{train,val}` to `rw-data/raw/ripvis/`, `data/replay/` to `rw-data/replay/`. Requires `RW_CONFIRM_APPLY=upload-data`. Writes a manifest JSON with file counts and bytes to `rw-artifacts/reports/data-manifest.json` | Run in Sprint 2 |
| `scripts/local_seed.py` | Creates all buckets, tables (with GSIs and TTL), queues, SSM params, SNS topic in moto server using names from config. Seeds `rw-cameras` with `cam-01` (demo beach coordinates, `seaward_vector`, `replay_prefix`) | Local only |

### N-14 Bench harness (`rw/bench`)
- `python -m rw.bench --inputs <dir or s3 prefix> --runtime <cool|std-arm|std-x86> --frames 300 --warmup 50 --out work/bench/<run_id>/`.
- Runs the baseline pipeline stage by stage on the same frames, records per-frame latency for `decode`, `preprocess`, `stabilize`, `timex`, `flow`, `detect`, total; FPS; CPU % (from `/proc/self/stat` deltas); max RSS (`resource.getrusage`); instance type (IMDSv2 if available, else `local`); `check_cv2_runtime()` output; `cv2.getBuildInformation()` saved to `build_info.txt`.
- Cost per camera-hour: `(instance_price_per_h + cool_fee_per_h) * (processing_time_per_s_of_video)` with prices from `rw/bench/prices.yaml` (`c7g.large: 0.0725`, `c7i.large: 0.08925`, `cool_fee: 0.01`, each with a `source` URL and `checked_on` date; Claude Code re-checks these on the AWS pricing pages before Sprint 2 and updates the date).
- Writes `results.csv`, `summary.json`, and a PNG chart per stage (matplotlib in the `dev` group only).
- `scripts/bench_remote.sh` (Sprint 2+): launches one Spot `c7g.large` with the Ubuntu 24.04 arm64 AMI from SSM `/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id` and one Spot `c7i.large` with the amd64 equivalent, `instance-initiated-shutdown-behavior terminate`, user data that installs the release with `RW_RUNTIME=std-arm|std-x86`, runs the bench, uploads to `rw-artifacts/benchmarks/<run_id>/`, then `shutdown -h now`. The `cool` run happens on the worker via SSM.
- [ ] Run `make bench-local` on a laptop with the synthetic clip and commit nothing but the code. Paste the summary into `handoff.md` labeled "laptop, not a benchmark result".

### N-15 CI (`.github/workflows/`)
Pin every action to a full commit SHA with the version in a comment. Claude Code looks up the current latest major versions at the time of writing.

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | `pull_request`, push to `main` | `python`: setup uv, `uv sync --extra dev --extra cv-std`, ruff check, ruff format --check, pytest unit + integration (moto server started in the job), schema drift check. `security`: gitleaks, semgrep (`p/python`, `p/terraform`, `p/secrets`), `uvx pip-audit`. `terraform`: setup terraform `1.10.x` or newer 1.x, fmt check, `tf-validate`, tflint, checkov. `shell`: shellcheck |
| `deploy.yml` | push to `main`, manual | First step: exit successfully with a notice if repository variable `RW_DEPLOY_ENABLED != 'true'`. Otherwise: OIDC login as `rw-gha`, terraform apply changed stacks in order, `make release`, upload release to S3, set `/rw/release`, send SSM `rw-deploy` to instances tagged `rw:role=worker` if the ASG has an instance, deploy Lambdas (via terraform), upload `frontend/dist` (or the placeholder), CloudFront invalidation `/*` |
| `bench.yml` | manual | Guarded by the same variable. Runs `make bench` with `RW_CONFIRM_APPLY=bench` |
| `eval.yml` | manual | Guarded. Placeholder job that runs `python -m rw.eval --split val` (module created as a stub with a TODO for Saif) |

Permissions: `contents: read` by default, `id-token: write` only on jobs that log in to AWS, `pull-requests: write` only where comments are posted.

---

## 9. Daksh's tasks

### D-01 Contracts
- [x] Implement 7.1 to 7.6 as Pydantic v2 models in `rw/contracts/` with `model_config = ConfigDict(extra="forbid")`, enums for every fixed value, validators for: polygon max 32 points, confidence in [0, 1], thresholds consistent with labels, size under 64 KB, `reason` required on reject.
- [x] `export_schemas.py` writes JSON Schemas to `docs/contracts/`.
- [x] `docs/contracts/README.md`: versioning rule, how to propose a change, owners.
- [x] Share the draft with Saif on Day 1. Record his sign-off (or requested changes) in the Decision Log with the date.
- [x] Tests: valid example round-trips for each model; invalid examples rejected (extra field, 33-point polygon, confidence 1.2, reject without reason).

### D-02 Trace and decision helpers
- [x] `rw/agent/trace.py`: `TraceWriter(trace_key, trace_id)` with `step(type, name, input, output_summary, reasoning_summary, latency_ms, error)` writing `TraceStep` rows with an incrementing `step`. `rekey(new_trace_key)` used when a candidate becomes an incident (copies existing steps to the incident key, so one incident has one complete trace).
- [x] Output summaries are truncated: max 2 KB per field, lists capped at 10 items. Full tool outputs are never stored in DynamoDB.

### D-03 MCP server and data tools (`rw/mcp_tools/`)
- [ ] FastMCP server, streamable HTTP on `127.0.0.1:8765`, no auth (loopback only; document why). Heartbeat. Structured logging with `trace_id` passed as a tool argument on every tool.
- [ ] Every tool has typed inputs and outputs (Pydantic), a docstring written for the model (what it does, when to use it, what it returns), and returns compact JSON.

| Tool | Inputs | Output | Behavior |
|---|---|---|---|
| `zoom_and_recheck` | `trace_id`, `result_id`, `camera_id`, `rip_id` or `bbox_px`, `zoom` (1.5 to 4.0, default 2.0) | `label`, `confidence`, `before_confidence`, `keyframes_used`, `note` | Loads up to 10 keyframes for the result from S3, crops the bbox expanded by 25%, upsamples by `zoom` with `INTER_CUBIC`, calls `VisionPipeline.recheck`. Fails gracefully with `note="no_keyframes"` |
| `get_flow_stats` | `trace_id`, `camera_id`, `window_s` (30 to 300) | per-rip mean and max seaward flow, persistence in seconds, trend (`rising`, `steady`, `falling`), number of clips seen | Queries `rw-detections` for the camera over the window |
| `track_swimmers` | `trace_id`, `result_id`, `camera_id` | swimmers with `track_id`, `in_rip_id`, distance, drift, count at risk | Reads the result |
| `predict_spread` | `trace_id`, `result_id`, `camera_id`, `rip_id`, `horizons_s` (default `[60, 180, 300]`) | predicted polygons per horizon, swimmers predicted inside the rip per horizon, `method` | Baseline: keep the rip anchored at its shore end, extend its seaward end by seaward flow speed x time (capped at 50% of the rip length by 300 s) and widen it 10% per minute, both scaled by the ocean factor below; advect swimmer positions with their drift. Method string `"seaward_stretch_v1"` (`"no_motion"` in image mode). Saif can replace later |
| `get_ocean_conditions` | `trace_id` | next high/low tide times and heights, tide trend, active NWS alerts (event + headline), `ocean_factor` (1.0 normal, 1.2 within 2 h of low tide, +0.3 if a Rip Current Statement or Beach Hazards Statement is active), `age_minutes` | Reads SSM `/rw/ocean/latest`. If older than 120 min, returns the data with `stale=true` |

### D-04 MCP action tools

| Tool | Inputs | Output | Behavior |
|---|---|---|---|
| `create_incident` | `trace_id`, `camera_id`, `result_id`, `risk_level`, `summary` | `incident_id` | Creates `rw-incidents` row (`status=watching`), renders an annotated snapshot (rip polygon, swimmer boxes, label, confidence, timestamp, "RipWatch" caption) from the first keyframe with `rw.vision.draw` to `evidence/<incident_id>/snapshot.jpg`. Idempotent per `result_id` |
| `set_watch` | `trace_id`, `incident_id`, `clips` (1 to 6), `reason` | new status, `watch_until_clips` | Status `watching`, stores remaining follow-up clips |
| `alert_lifeguard` | `trace_id`, `incident_id`, `message` (max 300 chars) | `alerted: true`, `sns_message_id` | Publishes to `rw-lifeguard-alerts` with incident link placeholder `https://<cdn>/incidents/<id>` (domain from env), status `alerted` |
| `request_approval` | `trace_id`, `incident_id`, `action` (`raise_red_flag`, `pa_announcement`, `dispatch_lifeguard`), `message` | `approval_requested: true` | Sets `pending_action` and `status=alerted` on the incident. Never performs the action |
| `request_followup_capture` | `trace_id`, `camera_id`, `job_id`, `message` | `requested: true` | Writes `followup_request` on the job (shown in the dashboard). Only allowed for `mode=image` or `burst` |
| `close_incident` | `trace_id`, `incident_id`, `outcome` (`false_alarm`, `resolved`, `confirmed`), `reason` | final status | Sets `resolved`/`confirmed`/`closed`. `false_alarm` emits `FalseAlarmsRejected` |

- [ ] Unit tests for every tool with moto (S3, DynamoDB, SNS, SSM) and fixture keyframes.

### D-05 Agent loop (`rw/agent/`)

**Process:** `rw-agent` long-polls `rw-candidates` (wait 20 s, max 1, visibility 120 s). For each message:

1. Validate `CandidateMessage`. Start `TraceWriter(trace_key=f"cand_{result_id}")`, step `candidate_received`.
2. **Cooldown (`cooldown.py`):** if this camera had a decision in the last `/rw/agent/cooldown_s` (default 60) for a rip whose polygon IoU with the current top rip is > 0.3, and there is no active incident, write `suppressed_duplicate`, delete the message, stop. No Bedrock call.
3. Load `VisionResult` from `rw-detections`, active incident (if any) from `rw-incidents`.
4. **Pre-risk (`risk.py`):** deterministic risk level from section 9.5 rules. Passed to the model as context and used by fallback.
5. **LLM loop (`loop.py`):** Bedrock Converse with `toolConfig` built from the MCP server's tool list (fetched once at startup with an MCP client, converted to Bedrock `toolSpec` JSON schemas) plus one local tool `submit_decision` (schema = `AgentDecision` fields `decision`, `risk_level`, `reasons`, `requested_action`). Loop: send, if `stopReason == "tool_use"` call the tools through MCP, append `toolResult`, repeat. Stop when `submit_decision` is called. Hard limits: max 6 MCP tool calls, max 8 model turns, 20 s per model call, 60 s total. `inferenceConfig`: `maxTokens 800`, `temperature 0.2`.
6. **Validation:** the decision must be consistent with tool actions taken (for example `alert` requires that `request_approval` or `alert_lifeguard` was called; if not, the loop calls them itself and records a `status_change` step noting the correction).
7. **Fallback (`fallback.py`):** on any Bedrock error, timeout, limit hit, or invalid decision: step `fallback` with the reason, then apply the deterministic policy: `CRITICAL`/`HIGH` → `create_incident` + `alert_lifeguard` + `request_approval(raise_red_flag)`; `ELEVATED` → `create_incident` + `set_watch(3)`; `LOW` → `ignore`. Emit `AgentFallbackUsed`.
8. If an incident was created, `TraceWriter.rekey(incident_id)`. Write the `AgentDecision` as the final `decision` step and on the incident row (`last_decision`).
9. Emit metrics: `AgentToolCalls`, `AgentDecisionLatencyMs`, `BedrockTokens` (input + output), `IncidentsCreated`. Delete the SQS message only after all writes succeed.

**`llm.py`:** `LLMClient` protocol with `converse(messages, system, tool_config) -> response`. `BedrockLLM` uses `bedrock-runtime.converse` with model id from `/rw/bedrock/model_id`. `FakeLLM` (`fake_llm.py`) replays scripted responses from a YAML file (`tests/fixtures/llm_scripts/*.yaml`) selected by scenario, so tests and `local-demo` are deterministic. Selected with `RW_LLM=bedrock|fake`.

**`prompts.py`:** system prompt (kept under 600 tokens) that states: role (lifeguard assistant, never takes public action without approval), tools and when to use them, the decision rules, the requirement to zoom before alerting when confidence < 0.85 or glare > 0.3, image mode rules (request follow-up capture when uncertain), and the output contract (always end with `submit_decision`). The user message contains a compact JSON summary of the candidate, result summary, top 3 rips, swimmers at risk, quality, active incident, and pre-risk level. Never the full result.

- [ ] Tests with `FakeLLM` scenarios: (a) confident rip with swimmer → zoom → alert + approval request; (b) uncertain rip → zoom → confidence drops → `close_false_alarm`, `FalseAlarmsRejected` emitted; (c) image mode uncertain → `request_followup_capture` → `watch`; (d) Bedrock raises → fallback path; (e) model loops tools 7 times → limit → fallback; (f) duplicate within cooldown → suppressed, no LLM call. Each test asserts the trace steps in order.

### D-06 Incident lifecycle and watching (`lifecycle.py`)

```
watching --(agent alert)--> alerted --(human approve)--> approved --(agent verify)--> confirmed --> closed
   |                          |                                        |
   |                          +--(human reject)--> rejected --> closed  +--(rip gone 3 clips)--> resolved --> closed
   +--(rip gone for N follow-up clips)--> resolved --> closed
   +--(agent decides false alarm)--> closed (outcome false_alarm)
```

- [ ] `transition(incident, event)` is the only way to change status; invalid transitions raise. Every transition writes a `status_change` trace step.
- [ ] Follow-up handling: when a candidate has `reason=active_incident_followup`, the agent runs with the incident context. For `watching` incidents it decrements `watch_until_clips`; when it reaches 0 with the rip absent in the last 3 results, it resolves without calling Bedrock (rule-based, recorded). For `approved` incidents, 2 follow-up results confirming the rip → `confirmed`; 3 clear results → `resolved`.
- [ ] Unit tests for every allowed and disallowed transition.

### D-07 `rw-api` Lambda (`lambdas/api/`)
Single handler, routes on `event["routeKey"]`. Auth claims from `event["requestContext"]["authorizer"]["jwt"]["claims"]`. All responses JSON with `Content-Type`, no stack traces leaked, errors as `{"error": code, "message": text}`.

| Route | Auth | Does |
|---|---|---|
| `GET /api/health` | No | `{"ok": true, "version": <sha>}` |
| `GET /api/cameras` | Yes | Cameras with `beach_flag`, coordinates, latest status |
| `GET /api/cameras/{camera_id}/detections?since=<iso>&limit=<n≤50>` | Yes | Latest results (summary, rips, swimmers, keyframe keys) for overlays |
| `GET /api/incidents?status=<s>&limit=<n≤50>` | Yes | Via `status-index` |
| `GET /api/incidents/{incident_id}` | Yes | Incident + last decision + evidence presigned GET (5 min) |
| `GET /api/incidents/{incident_id}/trace` | Yes | All `TraceStep`s in order |
| `POST /api/incidents/{incident_id}/approval` | Yes | Body `{decision, action, reason?}`. Only when status `alerted` and `action == pending_action`. Writes `Approval`, transitions incident (shared lifecycle rules, imported from a copy-free shared module, see note), on approve of `raise_red_flag` sets `beach_flag=red` on the camera, publishes a confirmation to `rw-lifeguard-alerts`, emits `ApprovalLatencySec` |
| `POST /api/uploads` | Yes | Body `{filename, content_type, size_bytes, camera_id?}`. Validates type (`video/mp4`, `video/quicktime`, `image/jpeg`, `image/png`, `application/zip`) and size ≤ 200 MB. Creates `rw-jobs` row `awaiting_upload`. Returns presigned POST for `incoming/<camera_id or "upload">/<job_id>.<ext>` with `content-length-range` and exact `Content-Type`, expiry 10 min |
| `GET /api/jobs/{job_id}` | Yes | Job row + result summary + `followup_request` |
| `GET /api/media?key=<s3 key>` | Yes | Presigned GET (5 min) only for allowed prefixes: `replay/`, `incoming/` in data bucket, `evidence/`, `keyframes/` in artifacts bucket. Anything else 403 |

**Shared code note:** Lambdas must stay stdlib + boto3. The incident lifecycle rules and contract enums needed by `rw-api` live in `rw/contracts/` and `rw/agent/lifecycle.py` without Pydantic imports in the parts the Lambda uses (`lifecycle_rules.py` with plain dicts). The `lambda-fn` module packages `lambdas/api/` plus a copy of `rw/contracts/enums.py` and `rw/agent/lifecycle_rules.py` into the zip (Terraform `archive_file` with a `source` block per file). A test asserts the Lambda imports only stdlib + boto3.

- [ ] Unit tests per route with moto and fake JWT claims, including: approval on wrong status → 409, reject without reason → 400, media outside allowed prefixes → 403, upload with bad type → 400.

### D-08 `rw-ocean-poller` Lambda (`lambdas/ocean_poller/`)
- NOAA CO-OPS: `GET https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&station=<id>&datum=MLLW&units=metric&time_zone=gmt&interval=hilo&format=json&begin_date=<today>&range=48&application=RipWatch`.
- NWS: `GET https://api.weather.gov/alerts/active?zone=<zone_id>` with headers `User-Agent: <RW_NWS_USER_AGENT>` and `Accept: application/geo+json`. Keep alerts whose `event` contains "Rip Current" or "Beach Hazards" or "High Surf".
- Writes compact JSON (must stay under 4 KB, standard SSM tier) to `/rw/ocean/latest`: `fetched_at`, `station`, next 4 tide events, `tide_trend`, up to 3 alerts (`event`, `headline` truncated to 160 chars, `expires`). Uses `urllib.request` with 10 s timeout. On partial failure keeps the last good section and sets `errors`.
- [ ] Unit tests with recorded fixture responses (no network). One test marked `network` that hits the real APIs for the chosen station and zone, skipped in CI.

### D-09 Local integration tests (`tests/integration/`)
- [ ] Fixture starts moto server, runs `local_seed.py`, starts `rw-mcp-tools` in a thread.
- [ ] Test: upload synthetic clip to `incoming/cam-01/` → manually send the S3-style event to `rw-jobs` (moto server does not deliver S3 notifications reliably; document this) → ingest processes → candidate sent → agent with `FakeLLM` scenario (a) → incident `alerted` → `rw-api` handler invoked directly with approval → incident `approved` → two follow-up candidates → `confirmed`. Asserts the full trace and all metrics emitted.
- [ ] Test: image upload uncertain → follow-up request on the job.

---

## 10. Joint task J-01: local end-to-end demo

`make local-up && make local-demo` must:
1. Start moto server, seed resources, generate the synthetic clip.
2. Start `rw-mcp-tools` and run camera-sim, ingest, vision and agent in one process (threads) with `RW_LLM=fake`.
3. Feed 6 clips (3 with a rip, 3 clear) and one single image.
4. Print a readable report: each clip's status, each agent decision with tool calls, the incident lifecycle, and the full trace of one incident.
5. Approve the incident by calling the `rw-api` handler directly, then show the follow-up confirmation.
6. Exit 0 if every expected state was reached, else 1.

Record a terminal recording or screenshot in `handoff.md` for the sprint review.

---

## 11. Rules for Claude Code during this sprint

1. Read this file and `docs/02-infra-north-star.md` before starting any task.
2. Plan freely. Never run `apply`, `destroy`, or any mutating `aws` command unless Noufa or Daksh has said "apply" (or "destroy") for that exact stack in the current session, and has confirmed the plan summary with "yes". Never use `-auto-approve`. Never read or print `~/.aws/credentials`.
3. Never invent account IDs, emails, station IDs, zone IDs, AMI IDs, or ARNs. Ask (section 2).
4. Use only names from section 5. If a new resource seems necessary, stop and propose it: name, why, cost, north star section it fits.
5. No new dependencies beyond section 3 without asking. Lambdas: stdlib + boto3 only.
6. No secrets in code, tests, logs, or docs. Gitleaks must pass.
7. Every task ends with tests passing and `make check` green for the parts it touched.
8. Tick the task checkbox, add a `handoff.md` session entry, and add a Decision Log entry for any choice not already written here.
9. If something here is wrong or impossible, say so, propose the smallest fix, and wait for agreement before changing direction.
10. No em dashes in any file.

---

## 12. Risks this sprint and what we do about them

| Risk | Impact | Mitigation |
|---|---|---|
| Saif's model interface differs from `Detector` | Rework in Sprint 2 | D-01 on Day 1 includes the `Detector` protocol; Saif signs off on both |
| moto behavior differs from AWS (S3 notifications, Bedrock) | Surprises in Sprint 2 | Integration tests send SQS events directly; Bedrock is always faked; Sprint 2 starts with smoke tests on real AWS |
| COOL turns out to be OpenCV 4 | Runtime decision changes | Code checks `RW_RUNTIME` only; switching to `std-arm` is one variable (north star 0) |
| 2 vCPUs too slow for 5 fps flow | Real-time demo lags | Flow runs at 1/2 resolution, configurable via SSM; bench-local gives an early signal |
| Terraform two-pass dependencies (CloudFront domain needed by Cognito callbacks and bucket CORS) | Apply fails | Variables with placeholders, documented second apply in Sprint 2 |
| Scope too big for 7 days | Unfinished tasks | Order in 6.2 is the priority. Anything unfinished moves to the top of Sprint 2, logged |

---

## 13. Sprint 2 preview (for context only, do not start)

Upgrade to Paid plan, apply bootstrap (if not done in Sprint 1), migrate bootstrap state, apply iam and create access keys for Daksh and Saif, `make up` stack by stack, subscribe COOL, run the verification gate, set `cool_ami_id`, upload data, first real deploy, Bedrock smoke test, real end-to-end on AWS with the baseline pipeline, first real benchmark. Saif's detector integrated.

---

## 14. Decision Log

| Date | Decision | Why | By |
|---|---|---|---|
| 2026-10-01 | Sprint 1 writes and tests all code, provisions nothing | Catch design mistakes while they are free | Noufa |
| 2026-10-01 | Local AWS is moto server, not LocalStack | Fully open source, no account or token needed | Noufa |
| 2026-10-01 | `rw-agent-trace` key is `trace_key` (incident id or `cand_<result_id>`) | Ignored candidates need traces too | Noufa, Daksh |
| 2026-10-01 | `rw-detections` key is `camera_id` + `ts_result` | Latest results per camera in one query | Noufa |
| 2026-10-01 | Ingest runs the vision pipeline in-process; `rw-vision` unit handles active-incident refresh, heartbeat and model loading | Frames never cross process boundaries; less memory and complexity | Noufa |
| 2026-10-01 | Dedicated `rw-kill-switch` SNS topic fed only by the 80% budget alert | So ordinary alarms never trigger the kill switch | Noufa |
| 2026-10-01 | Burst uploads arrive as one `.zip` | One S3 object = one job, no partial batches | Noufa |
| 2026-10-01 | Local AWS access via `aws configure --profile ripwatch` with per-person IAM users and access keys, instead of IAM Identity Center | Simpler for a 3-person, 4-week project; no Organization needed. Keys stay in `~/.aws/credentials`, Gitleaks guards the repo, MFA required for console | Noufa |
| 2026-10-01 | `terraform plan` against real AWS is allowed anytime; apply and destroy only after a human says "apply" and confirms the plan | Catch real errors early without risking cost or changes | Noufa, Daksh |
| 2026-10-01 | Lighter path (section 0.4) defined, decision point Sun Oct 4 | A known fallback if scope is too big | Noufa |
| 2026-10-02 | Contracts: Approval lives in `rw/contracts/decision.py`, Job in `vision.py`, shared types in `base.py`; all 6 models export schemas (not only 3) | Keep the section 4 file list; D-01 asks for every model | Daksh |
| 2026-10-02 | Job gets `schema_version` (default `1.0`) and extra consistency checks (counts match lists, `in_rip_id` exists, polygon inside frame, motion null in image mode, alert needs `incident_id`, `requested_action` only on alert) | Versioning rule says every message has a version; catch bad data at the boundary | Daksh |
| 2026-10-02 | Thresholds 0.70 / 0.40 are defaults only; live SSM values passed as validation context | Saif's model needs tuned thresholds | Daksh |
| 2026-10-02 | Saif reviewed the VisionResult draft: requested changes (box format pick one, original-image pixel coords + image size, temporal evidence N of M frames, `zoom_and_recheck` before/after status). Two drafts exist (his and Daksh's); merge at the sync, sign-off after | Recorded per D-01 | Saif, Daksh |
| 2026-10-02 | `TraceWriter` writes through a `TraceSink` interface; in-memory sink now, DynamoDB sink after N-04 | Not blocked on `rw.common.aws` | Daksh |
| 2026-10-03 | `predict_spread` uses `seaward_stretch_v1` instead of `linear_advection_v1`: shore end fixed, seaward end extends by flow speed x time (capped at 50% of rip length by 300 s), widens 10%/min, scaled by ocean factor | Translating the whole polygon at water speed pushed the example rip (6.4 px/s, 640x360) out of frame within 1 minute; water flows through a rip, the rip itself mostly stays | Daksh |
| | | | |

---

## 15. Progress Log

Progress, sessions, task statuses and requests between Noufa and Daksh live in `handoff.md` at the repo root (format in `cloud-claude.md` section 8). This file keeps the task definitions, checkboxes and the Decision Log.
