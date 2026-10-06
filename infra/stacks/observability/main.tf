# Observability (sprint-1.md N-09, north star 14): the observability module wired to real names.
# Every name is fixed by section 5, so nothing is read here; apply it last.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  lambdas  = ["rw-api", "rw-ocean-poller", "rw-scheduler", "rw-kill-switch"]
  services = ["rw-camera-sim", "rw-ingest", "rw-vision", "rw-mcp-tools", "rw-agent", "deploy"]
}

module "observability" {
  source          = "../../modules/observability"
  namespace       = "RipWatch"
  queue_names     = ["rw-jobs", "rw-candidates"]
  dlq_names       = ["rw-jobs-dlq", "rw-candidates-dlq"]
  asg_name        = "rw-worker-asg"
  api_lambda_name = "rw-api"
  lambda_names    = local.lambdas
  ops_topic_arn   = "arn:aws:sns:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:rw-ops-alerts"
  log_group_names = concat(
    [for s in local.services : "/rw/worker/${s}"],
    [for f in local.lambdas : "/aws/lambda/${f}"],
    ["/aws/apigateway/rw-http-api"],
  )
  worker_runtime_alarm_enabled = !var.judging_mode
}
