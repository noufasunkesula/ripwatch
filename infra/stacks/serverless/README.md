# stacks/serverless

North star 10, 12, 13 and sprint-1.md N-09.

| Creates | Notes |
|---|---|
| SNS `rw-lifeguard-alerts` | Email subscription to `lifeguard_email` (confirm the AWS email) |
| Cognito `rw-users` | `cognito` module; callback URLs from `dashboard_url` |
| Lambdas `rw-api` (15 s), `rw-ocean-poller` (30 s), `rw-scheduler` (120 s), `rw-kill-switch` (30 s) | `lambda-fn` module, each with only its own permissions. `rw-api` also packages `rw/contracts/enums.py` and `rw/agent/lifecycle_rules.py` once they exist |
| `rw-kill-switch` subscription | To the bootstrap `rw-kill-switch` topic, plus the Lambda permission |
| EventBridge Scheduler (Asia/Kolkata) | `rw-sleep-nightly` 01:00 (disabled when `judging_mode = true`), `rw-export-nightly` 01:15, `rw-ocean-poll` every 30 min |
| HTTP API `rw-http-api` | Cognito JWT on every route except `GET /api/health`, Lambda proxy (payload v2), `$default` stage, 20 rps / burst 50, access logs `/aws/apigateway/rw-http-api` (7 days) |

**Inputs:** `lifeguard_email` (required), `dashboard_url`, `noaa_station_id`, `nws_zone_id`, `nws_user_agent`, `judging_mode`.

**Outputs:** `api_endpoint`, `api_domain`, `user_pool_id`, `client_id`, `cognito_domain`, `lifeguard_topic_arn`, `lambda_names`.

**Apply order:** after `bootstrap` (kill-switch and ops topics), `data` and `compute` (tables, buckets, ASG). Before `edge`. Second apply after `edge` with the real `dashboard_url`.
