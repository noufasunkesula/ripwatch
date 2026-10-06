# stacks/compute

The worker (sprint-1.md N-09, north star 8).

| Creates | Notes |
|---|---|
| `rw-worker-role` + instance profile | SSM core, CloudWatch agent, and a least-privilege inline policy: read `rw-data`, write `incoming/` (camera sim), read/write `rw-artifacts` work prefixes, read `releases/` and `models/`, CRUD on the 6 tables and indexes, consume `rw-jobs`, send/receive `rw-candidates`, publish `rw-lifeguard-alerts`, invoke the configured Bedrock model, read `/rw/*` (never write) |
| `rw-worker-lt` + `rw-worker-asg` | `worker-asg` module, 0/1/0, user data from `deploy/user_data.sh` |
| Log groups `/rw/worker/<service>` | 6 groups, 7 days |
| SSM document `rw-deploy` | `deploy/ssm/rw-deploy.yaml` |
| SSM parameter `/rw/cloudwatch-agent/config` | `deploy/cloudwatch-agent.json` |

**Inputs:** `cool_ami_id` (required; empty fails the plan with a pointer to `docs/sprint-2-inputs.md`), `instance_type`, `runtime`, `bedrock_model_id`.

**Outputs:** `asg_name`, `launch_template_id`, `worker_role_arn`, `deploy_document`.

**Apply order:** after `network` (VPC, subnets, `rw-worker-sg` are read by name) and `data` (tables, queues, buckets). Before `serverless`.
