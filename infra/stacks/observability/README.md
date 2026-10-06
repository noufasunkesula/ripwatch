# stacks/observability

The `observability` module wired to the section 5 names: dashboards `rw-ops`, `rw-agent`,
`rw-benchmark`; alarms `rw-fps-low`, `rw-jobs-dlq-not-empty`, `rw-candidates-dlq-not-empty`,
`rw-agent-fallback-high`, `rw-api-errors`, `rw-worker-running-long` (to `rw-ops-alerts`); saved
queries `rw-errors`, `rw-slow-frames`, `rw-incident-timeline`.

**Inputs:** `judging_mode` (true disables `rw-worker-running-long`).

**Outputs:** `dashboard_names`, `alarm_names`.

**Apply order:** last, after every other stack (saved queries need the log groups to exist).
