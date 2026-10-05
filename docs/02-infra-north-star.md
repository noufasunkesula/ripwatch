# RipWatch: Infrastructure North Star (LOCKED)

Every AWS resource we will build, what it is, and why it exists. Nothing here is optional and nothing is "maybe". If it is not in this file, we do not build it. Claude Code and every sprint doc follow this file.

**Prefix:** `rw` · **Environment:** one · **Account:** one, on the AWS **Paid plan** · **Region:** `us-east-1`

---

## 0. Verified facts this plan is built on

| Fact | Source | What it means for us |
|---|---|---|
| COOL is an **AMI** on AWS Marketplace: Ubuntu 24.04, arm64, Graviton3 (`c7g`). OpenCV lives in Python venvs under `/opt/cool`, imported as `import cv2` | AWS Marketplace listing, AWS Spatial blog | We run our code **directly on the COOL machine**, not in containers |
| COOL software fee: **$0.01 per instance-hour** (listed for `c7g.medium` and `c7g.xlarge`), 7-day free trial | AWS Marketplace listing | Negligible. Subscribe in benchmark week so the trial covers it |
| COOL accelerates 78+ functions in core, imgcodecs, imgproc, videoio (resize, filters, color conversion, contours). **Optical flow is not listed** | AWS Spatial blog | Benchmark reports speedup **per pipeline stage**, honestly |
| OpenCV 5 is on PyPI: `opencv-python-headless==5.0.0.93` (July 2, 2026), wheels for x86_64 and aarch64 | PyPI | Standard OpenCV 5 baseline is a pinned pip install. Certain |
| Free plan blocks "certain AWS Marketplace offers that can incur charges". Credits carry over when upgrading to Paid | AWS Billing docs | **Upgrade to the Paid plan on day 1.** Removes every access doubt for COOL and Bedrock |
| RipVIS: 184 MP4 videos (112 train, 36 val, 36 test) + `sampled_images.zip` frames with YOLO and COCO labels. **Test split has no labels.** License CC BY-NC 4.0, no redistribution | Hugging Face dataset card | Evaluate on **val** (has labels). Use **test videos for the live camera simulation** (no labels needed, never seen in training). Never public |

### Day-1 verification gate (10 minutes, costs cents)

Launch one COOL instance on the free trial and run:

```bash
source /opt/cool/venvs/python_3.12/bin/activate
python -c "import cv2; print(cv2.__version__); print(cv2.getBuildInformation())"
```

| Result | Decision |
|---|---|
| Version is `5.x` | COOL is our production vision runtime. Plan stands as written |
| Version is `4.x` | Production runtime is standard OpenCV 5 on Graviton (pinned pip). COOL becomes the benchmark comparison only. **No other part of this plan changes** |

Either way the architecture below is identical. Only the Python environment on the worker differs.

---

## 1. Cost rules

| Rule | Why |
|---|---|
| No load balancer, no NAT, no VPC interface endpoints, no WAF, no ECS, no container registry, no Fargate | Each one bills every hour or adds parts we do not need |
| Light work runs on Lambda, API Gateway HTTP API, SQS, DynamoDB on-demand, CloudFront, Cognito | All are pay-per-use with free tiers that cover our usage |
| Heavy work runs on **one `c7g.large`** (2 vCPU, 4 GB, Graviton3, on-demand) | Smallest instance that holds the whole pipeline. On-demand, so no capacity surprises |
| The worker sleeps when we are not working | It is our biggest cost. Scheduled sleep + `make sleep` |
| Frames downscaled to 640 px wide, optical flow at 5 fps | Rips move slowly. Lets 2 vCPUs keep up in real time |
| Training on Kaggle/Colab from Hugging Face | Free GPU, no AWS data transfer |
| Agent only runs on rip candidates, with cooldowns and a tool-call cap | Bedrock bills per token |
| Max ~12 custom metrics, 3 dashboards, under 10 alarms, 7-day log retention | Stays inside CloudWatch free allowances |
| Budget alerts + kill switch Lambda | Hard stop if anything runs away |

---

## 2. Architecture

```
  INPUTS (one path for everything)
  - Simulated camera: rw-camera-sim drops the next 10 s clip of a
    RipVIS test video into S3 every 10 s
  - Dashboard upload: video clip, image or photo burst
                         |
                         v
             S3 rw-data/incoming/<camera_id>/
                         |  S3 event
                         v
                   SQS rw-jobs
                         |
  +----------------------------------------------------------+
  | rw-worker  (c7g.large, COOL AMI, Ubuntu 24.04, systemd)  |
  |                                                          |
  |  rw-ingest --> rw-vision --> SQS rw-candidates --+       |
  |   (clip ->       |                               |       |
  |    frames @5fps) | keyframes -> S3               v       |
  |               rw-mcp-tools <------------- rw-agent       |
  |               (zoom_and_recheck reads keyframes)         |
  +-------------------------------------------|--------------+
                                              | Converse API
                                              v
                                   Amazon Bedrock (Nova Lite)
  DynamoDB: cameras, jobs, detections, incidents, agent-trace, approvals
                                              |
  Browser --> CloudFront --> S3 (dashboard)   |
                  |                           |
                  +-- /api/* --> API Gateway HTTP API --> Lambda rw-api
                                (Cognito JWT auth)
  SNS email alerts · CloudWatch logs, metrics, dashboards, alarms
```

**One event, end to end:** a 10 s clip lands in S3 → worker splits it into frames at 5 fps → vision finds a possible rip, saves keyframes and JSON → message on `rw-candidates` → agent asks Bedrock what to do → agent calls tools (zoom and recheck a keyframe, ocean data, predict) → decides ignore, keep watching, or alert → incident + approval request → lifeguard approves on the dashboard → agent keeps checking the next clips to confirm → every step logged with one `trace_id`.

**Why clips through S3 instead of a live stream:** one input path for the simulated camera and for user uploads, nothing is lost if the worker restarts (clips wait in the queue), and every clip is stored evidence. Cost: about 10 s of extra delay per detection, which we state honestly in the report.

**Continuity across clips:** `rw-ingest` processes clips of the same camera in order and keeps per-camera state (last frames, rolling timex, flow history, swimmer tracks) in memory, so motion analysis does not reset every 10 s.

**JSON contract:** the vision output schema (`docs/contracts/vision-result.schema.json`) is agreed between the vision and agent owners in Sprint 1 and validated in tests on both sides.

---

## 3. Inputs: video, streams and images

The pipeline accepts anything that becomes frames. An **adapter** converts each input into `Frame(image, timestamp, source_id, camera_id)`.

| Adapter | Input | Used for |
|---|---|---|
| `VideoFileSource` | `.mp4`, `.mov` | RipVIS replay, uploaded clips |
| `FrameFolderSource` | Ordered `.jpg` folder | RipVIS `sampled_images`, extracted frames |
| `ImageSource` | One image | Uploaded photo |
| `BurstSource` | 2 to 10 photos seconds apart | Uploaded photo burst |


| Mode | Triggered by | Runs | Skips |
|---|---|---|---|
| Video | Video, frame folder, stream | Everything: stabilize, timex, optical flow, detector, fusion, tracking, prediction | Nothing |
| Burst | 2+ photos | Alignment (ORB + homography), coarse motion, detector, swimmers | Timex, tracking, prediction |
| Image | 1 photo | Detector, appearance cues, swimmers | Motion, timex, tracking, prediction |

**Agent behavior per mode:** in image mode, when uncertain, the agent calls `request_followup_capture` and asks the user for a short clip or burst of that spot. Visual uncertainty changes the next action, which strengthens the Agentic award case.

---

## 4. Bootstrap (one time, from Noufa's laptop)

Folder: `infra/bootstrap/`

| Resource | What it is | Why |
|---|---|---|
| Account upgraded to **Paid plan** (console, once) | Billing plan | Marketplace (COOL) access guaranteed. Credits carry over |
| S3 `rw-tfstate-<account_id>` | Terraform state | Shared memory of what exists. Versioning on, encryption on, public access blocked, `prevent_destroy` |
| `use_lockfile = true` in every backend | State locking | Two people can never apply at the same time |
| IAM OIDC provider `token.actions.githubusercontent.com` | GitHub to AWS trust | CI logs in with short-lived tokens. No AWS keys stored in GitHub |
| IAM role `rw-gha` | CI identity | Plan, apply, upload releases, deploy. Trust limited to our repo |
| AWS Budgets `rw-monthly` ($50, alerts at 25/50/80/100%) | Spend alerts | We see spend early |
| AWS Budgets `rw-bedrock` ($10, Bedrock only) | AI spend alert | Catches an agent loop |
| Cost Anomaly Detection monitor + daily email | Spike detection | Free |
| CloudTrail `rw-trail` (management events) to `rw-artifacts/cloudtrail/` | Audit log | Who changed what. First trail is free |

Backend block for every stack:

```hcl
terraform {
  backend "s3" {
    bucket       = "rw-tfstate-<account_id>"
    key          = "<stack>/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

---

## 5. Identity and access

Folder: `infra/stacks/iam/`

| Resource | What it is | Why |
|---|---|---|
| IAM user `rw-noufa` (created by hand, `AdministratorAccess`) | Noufa's login, runs Terraform | Owns infrastructure |
| IAM users `rw-daksh`, `rw-saif` in group `rw-developers` (Terraform) | Personal logins. Console requires MFA. CLI via `aws configure --profile ripwatch` with each person's own access key (keys never in Terraform, repo or chat) | Traceable per person, no shared credentials |
| Group policy `rw-developers` | Read `rw-data`, read/write `rw-artifacts`, read logs and dashboards, invoke Bedrock, start SSM sessions on `rw-worker` | Can work fully, cannot delete data, state or billing |
| Explicit deny: delete on `rw-data/raw/*` and `rw-tfstate-*` for everyone except `rw-noufa` | Guardrail | One wrong command cannot cost us a day |
| Instance profile `rw-worker-role` | Identity of the worker | Read `rw-data`, write `rw-artifacts/{evidence,keyframes,benchmarks,eval,traces}`, copy within `rw-data/replay/` to `rw-data/incoming/`, read/write the 6 tables, send/receive both queues, invoke Bedrock, write logs and metrics, read `/rw/*` SSM params, SSM managed instance core |
| One role per Lambda | Lambda identities | Each Lambda gets only its own tables, buckets and actions |
| Judges | No AWS access | Dashboard URL, demo login, repo |

---

## 6. Networking

Folder: `infra/stacks/network/`

| Resource | What it is | Why |
|---|---|---|
| VPC `rw-vpc` `10.20.0.0/16` | Private network | Home for the worker |
| Subnets `rw-public-a` (`10.20.1.0/24`, us-east-1a), `rw-public-b` (`10.20.2.0/24`, us-east-1b) | Public subnets | Worker gets a public IP for outbound internet. Two AZs so the ASG can launch in either |
| Internet Gateway `rw-igw` + route table | Internet access | Worker reaches Bedrock, NOAA, NWS, PyPI. Free |
| Security group `rw-worker-sg` | Firewall | **No inbound rules at all.** Outbound 443 only. Nothing can connect in |
| Gateway endpoints for S3 and DynamoDB | Private routes to S3 and DynamoDB | Free, faster, keeps data traffic inside AWS |

No NAT: Lambdas run outside the VPC, and the worker uses its public IP for outbound. Access to the worker is only via SSM Session Manager.

---

## 7. Data

Folder: `infra/stacks/data/`

### 7.1 S3 buckets (all: public access blocked, SSE-S3 encryption, TLS-only policy)

| Bucket | Contents | Why |
|---|---|---|
| `rw-data-<account_id>` | `raw/ripvis/train/sampled_images/` + labels, `raw/ripvis/val/` (videos + frames + labels), `replay/clips/<camera_id>/` (RipVIS test videos pre-cut into 10 s clips), `incoming/<camera_id>/` (simulated camera + uploads, expires after 7 days), `calibration/` | Only what we use: ~10 to 15 GB, not 30 GB. Private per license |
| `rw-artifacts-<account_id>` | `releases/`, `models/`, `benchmarks/`, `eval/`, `traces/`, `evidence/` (annotated snapshots + short annotated clips, incidents only), `keyframes/` (1 fps JPEGs from clips with candidates, expires after 7 days), `cloudtrail/` (expires after 30 days) | All outputs and evidence in one place. Versioning on so results are never lost |
| `rw-frontend-<account_id>` | Dashboard build | Served only through CloudFront (Origin Access Control) |

### 7.2 DynamoDB (on-demand, encryption on)

| Table | Keys | Stores | Why |
|---|---|---|---|
| `rw-cameras` | PK `camera_id` | Location, field of view, calibration path, replay video | Cameras on the beach map |
| `rw-jobs` | PK `job_id` | Upload status, mode, result pointers | Upload progress in the dashboard |
| `rw-detections` | PK `camera_id`, SK `ts_result` (`<iso_ts>#<result_id>`); TTL 24h | Rip masks (simplified polygons), flow summary, swimmer boxes | Dashboard overlays, agent history. TTL deletes for free |
| `rw-incidents` | PK `incident_id`; GSI `status-index` (PK `status`, SK `created_at`) | Risk level, status (`watching`, `alerted`, `approved`, `rejected`, `confirmed`, `resolved`, `closed`), evidence paths | What lifeguards act on |
| `rw-agent-trace` | PK `trace_key` (incident id, or `cand_<result_id>` when no incident was created), SK `step` | Tool, input, output, reasoning summary, latency | Agentic award evidence |
| `rw-approvals` | PK `incident_id`, SK `ts` | Action, decision, user, reason | Human-in-the-loop evidence |

Nightly export of `rw-incidents`, `rw-agent-trace`, `rw-approvals` to `rw-artifacts/traces/` as JSON (by `rw-scheduler`). Evidence survives teardown.

### 7.3 Config

SSM Parameter Store, standard tier (free), all under `/rw/`:
`/rw/release` (git SHA to run), `/rw/bedrock/model_id`, `/rw/vision/frame_width`, `/rw/vision/flow_fps`, `/rw/risk/thresholds`, `/rw/agent/max_tool_calls`, `/rw/agent/cooldown_s`, `/rw/noaa/station_id`, `/rw/nws/zone_id`, `/rw/cool/enabled`.

---

## 8. The worker

Folder: `infra/stacks/compute/`

### 8.1 Machine

| Resource | What it is | Why |
|---|---|---|
| Launch template `rw-worker-lt` | COOL Graviton3 AMI, `c7g.large`, 30 GB gp3, IMDSv2 required, public IP, `rw-worker-sg`, `rw-worker-role`, user data | One reproducible definition |
| Auto Scaling Group `rw-worker-asg` | On-demand, min 0, max 1, both public subnets | Replaces the box if it dies. Desired 0 = asleep, 1 = awake |
| User data script | On boot: install SSM agent + CloudWatch agent if missing, create `/opt/rw`, download release `rw-artifacts/releases/<sha>.tar.gz` from `/rw/release`, install deps into the COOL venv with a constraints file that forbids `opencv-python*`, install systemd units, start services | Box goes from zero to running with no manual steps |

**Why on-demand and not Spot:** guaranteed availability during demos and judging, and no question about Marketplace AMI behavior on Spot. Cost difference over the hackathon is about $15.

### 8.2 Services (systemd units, one codebase in `/opt/rw`)

| Unit | Does | Memory cap |
|---|---|---|
| `rw-camera-sim` | Every 10 s, server-side copies the next clip from `replay/clips/<camera_id>/` to `incoming/<camera_id>/`. Loops when it reaches the end | 128 MB |
| `rw-ingest` | Pulls jobs from `rw-jobs`, downloads the clip/image, picks the adapter and mode, splits video into frames at 5 fps, keeps per-camera state across clips | 512 MB |
| `rw-vision` | OpenCV pipeline in video, burst or image mode. Writes JSON results to `rw-detections`, keyframes for candidate clips, evidence for incidents, metrics. Sends candidates (`rip` or `uncertain`) to `rw-candidates` | 2 GB |
| `rw-mcp-tools` | MCP server on `127.0.0.1:8765` with tools: `zoom_and_recheck` (crops a saved keyframe from S3 and reruns detection), `get_flow_stats`, `track_swimmers`, `predict_spread`, `get_ocean_conditions`, `create_incident`, `alert_lifeguard`, `request_approval`, `request_followup_capture`, `set_watch`, `close_incident` | 512 MB |
| `rw-agent` | Consumes `rw-candidates`, runs the Bedrock Converse tool-use loop through MCP, decides ignore / keep watching / alert, writes `rw-agent-trace`. For incidents in `watching` status, re-evaluates when the next clip result for that camera arrives | 512 MB |

**Incident lifecycle:** `watching` (agent unsure, re-checks the next clips) → `alerted` (approval requested) → `approved` / `rejected` → `confirmed` or `resolved` (agent verifies on later clips) → `closed`. Every transition is a row in `rw-agent-trace`.

Every unit starts with a **runtime check**: logs `cv2.__version__` and `cv2.__file__`, and exits if `cv2` is not loaded from the expected environment. This prevents a stray `opencv-python` silently replacing COOL, and the log line is our proof that COOL ran.

### 8.3 Models on the worker

| Model | Format | Runs with | Why |
|---|---|---|---|
| Rip segmenter (YOLOv8-seg trained on Kaggle) | ONNX in `rw-artifacts/models/` | `cv2.dnn` | No PyTorch or Ultralytics on the box. Smaller install, all inference through OpenCV |
| Person detector (OpenCV Zoo) | ONNX | `cv2.dnn` | Pretrained swimmer detection |

### 8.4 Deploys

| Step | How |
|---|---|
| CI packages the code | `rw-<sha>.tar.gz` uploaded to `rw-artifacts/releases/` |
| Point to the new release | CI sets `/rw/release` to the new SHA |
| Roll out | CI sends SSM Run Command `rw-deploy` to the tagged instance: download, install, restart units, health check |
| Rollback | Set `/rw/release` to the previous SHA and run `rw-deploy` |

No containers, no registry. Code is pure Python, so one package works on arm64 and x86.

---

## 9. Queues

| Resource | Why |
|---|---|
| SQS `rw-candidates` + DLQ `rw-candidates-dlq` (max receive 3) | Vision never waits for slow AI calls. Retries are automatic. Poison messages are isolated |
| SQS `rw-jobs` + DLQ `rw-jobs-dlq` (max receive 3), fed by S3 event on `rw-data/incoming/` | Clips and uploads wait safely in order while the worker is asleep or restarting |

---

## 10. Lambdas (Python 3.12, arm64, 256 MB, 7-day logs)

| Lambda | Trigger | Does |
|---|---|---|
| `rw-api` | API Gateway HTTP API | Cameras, detections, incidents, traces, approvals, job status, presigned upload URL (max 200 MB, allowed types only), presigned video URL (5 min) |
| `rw-ocean-poller` | EventBridge Scheduler, every 30 min | Fetches tide predictions from **NOAA CO-OPS** and active **NWS alerts** (Rip Current Statement, Beach Hazards Statement) for our zone. Caches in SSM `/rw/ocean/latest` |
| `rw-scheduler` | EventBridge Scheduler + manual invoke | `wake` (ASG desired 1), `sleep` (ASG desired 0), nightly trace export |
| `rw-kill-switch` | Dedicated SNS topic `rw-kill-switch`, fed only by the budget 80% alert | Sets ASG desired 0 and emails the team |

EventBridge Scheduler schedules: sleep at 01:00 IST daily, nightly export at 01:15 IST. Wake is manual (`make wake`) during build, and the box stays awake 24/7 during judging.

---

## 11. Bedrock

| Item | Value | Why |
|---|---|---|
| Model | **Amazon Nova Lite** (`amazon.nova-lite-v1:0`) via the Converse API with tool use | AWS's own model, available in `us-east-1`, supports tool calling, among the cheapest per token |
| Model ID in `/rw/bedrock/model_id` | Configurable | Swap models without a code change |
| Runs only on candidates | | Cost control |
| Cooldown 60 s per rip region | | No duplicate incidents |
| Max 6 tool calls per incident, 20 s timeout | | Stops loops |
| Rule-based fallback | If Bedrock errors or times out, deterministic policy alerts on `HIGH` and `CRITICAL` | System fails toward warning, never silence |
| Invocation logging to `rw-artifacts/bedrock-logs/` | | Judge evidence, cheaper in S3 than CloudWatch |

---

## 12. Frontend and edge

Folder: `infra/stacks/edge/`

| Resource | What it is | Why |
|---|---|---|
| CloudFront `rw-cdn` | HTTPS front door | Serves the dashboard from `rw-frontend`, routes `/api/*` to the HTTP API. One URL |
| API Gateway HTTP API `rw-http-api` | Backend entry | JWT authorizer from Cognito on every route, throttling 20 req/s, burst 50 |
| Cognito user pool `rw-users` + app client | Logins | Accounts for the team, `judge-demo`, `lifeguard-demo`. Approvals tied to a real user |
| Dashboard | React + Vite + HTML canvas | Polls the API every 2 s. Plays source video via presigned URL and draws rip masks, flow arrows and swimmer boxes from detection JSON. 2D beach twin on canvas. Upload page. Incident feed with trace timeline and approve/reject |

---

## 13. Notifications

| Topic | Subscribers | Why |
|---|---|---|
| `rw-lifeguard-alerts` | `lifeguard-demo` email | Alert on `HIGH`/`CRITICAL` incidents |
| `rw-ops-alerts` | Team emails | Alarms and budget alerts |
| `rw-kill-switch` | `rw-kill-switch` Lambda only | Budget 80% alert, scales the worker to 0 |

---

## 14. Observability

### 14.1 Logs
- JSON logs from all units and Lambdas with `trace_id`, `incident_id`, `source_id`, `frame_id`, `mode`.
- CloudWatch agent ships worker logs to `/rw/worker/<unit>`. Lambdas log to their default groups. All 7-day retention.
- INFO level. Per-frame details only at DEBUG, which is off in AWS.
- Saved Logs Insights queries: `rw-errors`, `rw-slow-frames`, `rw-incident-timeline`.

### 14.2 Metrics (Embedded Metric Format via CloudWatch agent, namespace `RipWatch`)

| Metric | Dimensions |
|---|---|
| `FrameLatencyMs` | `stage`, `runtime` (`cool` / `std-arm` / `std-x86`) |
| `FramesPerSecond` | none |
| `RipCandidates` | `mode` |
| `FalseAlarmsRejected` | none |
| `IncidentsCreated` | none |
| `AgentToolCalls` | none |
| `AgentDecisionLatencyMs` | none |
| `AgentFallbackUsed` | none |
| `BedrockTokens` | none |
| `JobsProcessed` | `mode` |
| `ApprovalLatencySec` | none |
| `CostPerCameraHour` | `runtime` |

### 14.3 Dashboards
`rw-ops` (FPS, latency by stage, queue depth, DLQs, jobs, errors) · `rw-agent` (candidates, false alarms rejected, incidents, tool calls, latency, fallbacks, tokens, approvals) · `rw-benchmark` (latency and cost per camera-hour across 3 runtimes).

### 14.4 Alarms (to `rw-ops-alerts`)
FPS below 4 for 5 min · either DLQ not empty · agent fallback above 20% in 15 min · `rw-api` errors above 5 in 5 min · worker running more than 14 h straight (build phase only, disabled during judging).

### 14.5 Agent evidence
`trace_id` on every log line and DynamoDB row · `rw-agent-trace` + nightly export · trace timeline in the dashboard · 3 to 5 curated traces in the report, including one image-mode trace that triggers `request_followup_capture`.

---

## 15. Security

| Control | Implementation |
|---|---|
| Least privilege | Roles scoped to exact buckets, prefixes, tables, queues |
| No inbound access | `rw-worker-sg` has zero inbound rules. SSM Session Manager only |
| IMDSv2 | Required in the launch template |
| Encryption | SSE-S3, DynamoDB encryption, TLS-only bucket policies |
| No public data | Block public access everywhere, CloudFront OAC, presigned URLs |
| Auth | Cognito JWT on every API route |
| Throttling and upload limits | API Gateway throttles, 200 MB cap, type allow-list, 7-day expiry |
| Audit | CloudTrail + `rw-approvals` |
| CI scanning | Gitleaks, Semgrep, tflint, checkov, pip-audit |
| Human approval | Every public action requires an authenticated approval |
| Fail-safe | Rule-based fallback alerts when the agent fails |
| Privacy | No face recognition, anonymous swimmer tracks, uploads auto-deleted |

---

## 16. CI/CD (GitHub Actions)

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | Pull request | ruff, pytest, Gitleaks, Semgrep, pip-audit, tflint, checkov, `terraform plan` as PR comment |
| `deploy.yml` | Merge to `main` | `terraform apply` per changed stack, package release to S3, set `/rw/release`, SSM `rw-deploy`, deploy Lambdas, build and upload frontend, CloudFront invalidation |
| `bench.yml` | Manual | Runs `make bench` |
| `eval.yml` | Manual | Runs evaluation on RipVIS val, writes to `rw-artifacts/eval/` |

All AWS access through `rw-gha` via OIDC.

---

## 17. Benchmark (COOL award)

| Item | Detail |
|---|---|
| Runtimes | `cool` on `c7g.large` (COOL AMI) · `std-arm` on `c7g.large` (Ubuntu 24.04 arm64 + `opencv-python-headless==5.0.0.93`) · `std-x86` on `c7i.large` (Ubuntu 24.04 x86 + same pip version) |
| Bench machines | `std-arm` and `std-x86` launched by `make bench` as Spot instances, run, upload results, terminate. `cool` runs on the worker |
| Inputs | Same 3 val videos, same frames, every run |
| Warm-up | First 50 frames discarded |
| Measured | Per-stage and total latency (p50, p95), FPS, CPU %, memory, cost per camera-hour (instance price + COOL fee) |
| Proof | `cv2.__version__`, `cv2.__file__`, `getBuildInformation()`, instance type, AMI ID saved per run |
| Output | CSV, JSON, charts in `rw-artifacts/benchmarks/<run_id>/` |

---

## 18. Reproducibility

- `Makefile`: `bootstrap`, `up`, `down`, `wake`, `sleep`, `deploy`, `data-subset`, `eval`, `bench`, `logs`, `ssm`.
- `uv` lockfile for all Python. `constraints.txt` forbids `opencv-python*` on the worker.
- Terraform `>= 1.10`, providers pinned, `.terraform.lock.hcl` committed.
- Fixed seeds for training and evaluation.
- `scripts/download_ripvis.py` pulls only the needed parts from Hugging Face and uploads to `rw-data`.
- Local dev: `make local` runs the 4 services on a laptop with pip OpenCV 5 against real S3, DynamoDB, SQS and Bedrock. No worker cost while developing.
- README: prerequisites, bootstrap, `make up`, `make wake`, open URL, `make sleep`, `make down`.

---

## 19. Repository layout

```
ripwatch/
  CLAUDE.md
  docs/
    01-project-description.md
    02-infra-north-star.md
    sprints/
  infra/
    bootstrap/
    modules/
      s3-bucket/  dynamodb-table/  sqs-queue/  lambda-fn/
      worker-asg/  cloudfront-site/  cognito/  observability/
    stacks/
      iam/  network/  data/  compute/  serverless/  edge/  observability/
    terraform.tfvars
  rw/                      # one Python package, all worker services
    adapters/  vision/  mcp_tools/  agent/  common/  bench/
  lambdas/
    api/  ocean_poller/  scheduler/  kill_switch/
  deploy/
    systemd/  user_data.sh  rw_deploy.sh  constraints.txt
  frontend/
  ml/
    training/  eval/  notebooks/
  scripts/
    download_ripvis.py
  .github/workflows/
  pyproject.toml  uv.lock  Makefile  README.md
```

---

## 20. Build order

| Phase | Builds | Done when |
|---|---|---|
| 1 | Paid plan upgrade, bootstrap, IAM users, budgets, kill switch, `rw-data`, `rw-artifacts`, data uploaded | All 3 can `aws sts get-caller-identity` with their own profile and list `rw-data`. Test budget alert reaches email |
| 2 | COOL verification gate | `cv2.__version__` recorded, runtime decision locked |
| 3 | Network, SSM params, `rw-worker-asg`, user data, systemd units, `rw-ingest` + `rw-vision`, `rw-detections`, logs + metrics, `rw-scheduler` | `make wake` → replayed video processed on AWS → FPS in CloudWatch → `make sleep` |
| 4 | `make bench` + `rw-benchmark` dashboard | 3-runtime table in S3 |
| 5 | Queues, `rw-mcp-tools`, `rw-agent`, Bedrock, incident/trace/approval tables, `rw-ocean-poller` | Detection → incident → approval request with trace, in video and image mode |
| 6 | `rw-api`, HTTP API, Cognito, CloudFront, frontend, uploads, SNS | Judge logs in, uploads a photo and a clip, approves an incident |
| 7 | Remaining dashboards, alarms, saved queries, nightly export | Any incident traceable end to end |
| 8 | `make down` + `make up` from scratch, CI green | Clean rebuild |
| 9 | Eval, bench, curated traces, diagrams | All report and video evidence in `rw-artifacts` |

---

## 21. Cost (build Oct 1 to 26, judging Oct 27 to Nov 9)

| Item | Assumption | Estimate |
|---|---|---|
| Worker `c7g.large` on-demand ($0.0725/h) + COOL ($0.01/h) | ~10 h/day x 25 days = 250 h | ~$21 |
| Worker during judging | 24/7 x 14 days = 336 h | ~$28 |
| Bench Spot instances | A few hours | <$1 |
| EBS 30 GB gp3 + public IPv4 | ~6 weeks | ~$8 |
| S3 ~15 GB | | <$1 |
| DynamoDB, Lambda, HTTP API, SQS, CloudFront, Cognito, SNS, SSM | Free tiers | ~$0 to $2 |
| CloudWatch | 12 metrics, lean logs | ~$5 to $7 |
| Bedrock Nova Lite | Guarded | ~$1 to $5 |
| **Total** | | **~$65 to $75** |

Covered by new-account credits ($100 base, up to $200). Budgets alert at $12.50, $25, $40, $50, and the kill switch fires at $40.

---

## 22. Not building (and why)

| Resource | Reason |
|---|---|
| ECS, ECR, Fargate, Docker on AWS | COOL ships as an AMI. Running natively is simpler and proves COOL directly |
| Application Load Balancer | ~$18/month fixed. HTTP API + Lambda does the job |
| NAT Gateway / instance, interface endpoints | Worker uses a public IP with zero inbound rules |
| WAF | Cognito auth + HTTP API throttling cover our risk |
| WebSockets, EventBridge bus | Polling every 2 s is enough |
| X-Ray / OpenTelemetry, Container Insights | `trace_id` in logs + `rw-agent-trace` gives the same evidence for free |
| Kinesis Video Streams, live RTSP ingest | Clip-based input covers the demo. Production path (documented in the report): an edge recorder at the beach uploads 10 s clips to the same `incoming/` prefix, no pipeline change |
| Secrets Manager | No secrets that need it. SSM is free |
| GuardDuty, custom domain, 3D twin, AWS GPU training | Cost or time without scoring benefit |
| Spot for the worker | Availability and Marketplace certainty matter more than ~$15 |

---

## 23. Definition of done

- [ ] `make up` builds everything from scratch, `make down` removes all but bootstrap and data
- [ ] `make wake` / `make sleep` work, kill switch tested
- [ ] COOL runtime verified and logged on every service start
- [ ] Video, burst and image inputs all work, agent adapts per mode
- [ ] Dashboard works with `judge-demo`: replay, uploads, incidents, traces, approvals
- [ ] `make bench` reproduces the 3-runtime table
- [ ] Every incident traceable from frame to approval
- [ ] CI green, no public buckets, no inbound ports, no AWS keys in the repo or in GitHub (CI uses OIDC)
- [ ] Total spend under $75
