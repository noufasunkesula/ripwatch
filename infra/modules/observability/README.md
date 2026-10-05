# observability

North star 14: 3 dashboards, the 14.4 alarms (to `rw-ops-alerts`) and 3 saved Logs Insights
queries. Stays inside CloudWatch free allowances (3 dashboards, under 10 alarms).

| Dashboard | Shows |
|---|---|
| `rw-ops` | FPS, latency by stage, queue depth, DLQs, jobs by mode, Lambda errors |
| `rw-agent` | candidates, false alarms rejected, incidents, tool calls, decision latency, fallbacks, tokens, approval latency |
| `rw-benchmark` | latency p50/p95 by runtime and stage, cost per camera-hour by runtime |

| Alarm | Fires when |
|---|---|
| `rw-fps-low` | FPS below 4 for 5 minutes |
| `<dlq>-not-empty` | any message in a DLQ (one per DLQ) |
| `rw-agent-fallback-high` | fallback above 20% of candidates in 15 minutes |
| `rw-api-errors` | more than 5 `rw-api` errors in 5 minutes |
| `rw-worker-running-long` | worker in service 14 h straight; set `worker_runtime_alarm_enabled = false` during judging |

Saved queries: `rw-errors`, `rw-slow-frames`, `rw-incident-timeline`.

Output: `dashboard_names`, `alarm_names`.
