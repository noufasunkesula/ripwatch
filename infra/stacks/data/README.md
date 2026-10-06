# stacks/data

North star 7 and sprint-1.md N-08.

| Kind | Names |
|---|---|
| Buckets | `rw-data-<acct>` (uploads expire after 7 days in `incoming/`, CORS POST/PUT from the dashboard), `rw-artifacts-<acct>` (versioned, old versions 14 days, `keyframes/` 7 days, `cloudtrail/` 30 days, CloudTrail and Bedrock may write), `rw-frontend-<acct>` |
| Tables | `rw-cameras`, `rw-jobs` (+`camera-index`), `rw-detections`, `rw-incidents` (+`status-index`), `rw-agent-trace`, `rw-approvals` |
| Queues | `rw-jobs` (300 s), `rw-candidates` (120 s), each with a DLQ |
| Events | `s3:ObjectCreated:*` on `incoming/*.{mp4,mov,jpg,jpeg,png,zip}` to `rw-jobs` |
| Config | SSM `/rw/...` standard tier (list in `main.tf`) |
| Audit | CloudTrail `rw-trail` (management events, this region) to `rw-artifacts/cloudtrail/`; Bedrock invocation logs (text only) to `rw-artifacts/bedrock-logs/` |

Two-pass apply (Sprint 2): `allowed_origin` is a placeholder until the `edge` stack exists; set
it to the CloudFront domain and apply again.

`/rw/release`, `/rw/ocean/latest` and `/rw/risk/thresholds` are created once and then owned by
CI, `rw-ocean-poller` and the agent; Terraform ignores their values.

Plan before the state bucket exists: `make plan-local STACK=data`.

Destroy needs `RW_CONFIRM_APPLY=destroy-data-really`; buckets holding objects still refuse to
delete (`force_destroy = false`).
