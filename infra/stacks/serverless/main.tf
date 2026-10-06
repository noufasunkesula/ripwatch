# Serverless (sprint-1.md N-09, north star 10, 12, 13): Lambdas, schedules, HTTP API, Cognito, SNS.
# Builds ARNs from the section 5 names, so data and compute must be applied first (section 0.3).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.region
  repo_root   = "${path.module}/../../.."
  data_bucket = "rw-data-${local.account_id}"
  art_bucket  = "rw-artifacts-${local.account_id}"
  asg_arn     = "arn:aws:autoscaling:${local.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/rw-worker-asg"
  ops_topic   = "arn:aws:sns:${local.region}:${local.account_id}:rw-ops-alerts"
  kill_topic  = "arn:aws:sns:${local.region}:${local.account_id}:rw-kill-switch"
  table_arn   = { for t in ["cameras", "jobs", "detections", "incidents", "agent-trace", "approvals"] : t => "arn:aws:dynamodb:${local.region}:${local.account_id}:table/rw-${t}" }
  table_env = {
    RW_TABLE_CAMERAS    = "rw-cameras"
    RW_TABLE_JOBS       = "rw-jobs"
    RW_TABLE_DETECTIONS = "rw-detections"
    RW_TABLE_INCIDENTS  = "rw-incidents"
    RW_TABLE_TRACE      = "rw-agent-trace"
    RW_TABLE_APPROVALS  = "rw-approvals"
  }
  # Shared stdlib-only modules rw-api packages (sprint-1.md D-07 note); added once Daksh writes them.
  api_shared = { for zip_path, src in {
    "rw_shared/enums.py"           = "${local.repo_root}/rw/contracts/enums.py"
    "rw_shared/lifecycle_rules.py" = "${local.repo_root}/rw/agent/lifecycle_rules.py"
  } : zip_path => src if fileexists(src) }
}

# ---------------------------------------------------------------- Lifeguard alerts and logins

resource "aws_sns_topic" "lifeguard" {
  #checkov:skip=CKV_AWS_26:Alert text is not sensitive; a CMK costs monthly
  name = "rw-lifeguard-alerts"
}

resource "aws_sns_topic_subscription" "lifeguard_email" {
  topic_arn = aws_sns_topic.lifeguard.arn
  protocol  = "email"
  endpoint  = var.lifeguard_email
}

module "cognito" {
  source        = "../../modules/cognito"
  name          = "rw-users"
  callback_urls = ["${var.dashboard_url}/"]
  logout_urls   = ["${var.dashboard_url}/"]
}

# ---------------------------------------------------------------- Lambdas

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadTables"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"]
    resources = concat(values(local.table_arn), [for a in values(local.table_arn) : "${a}/index/*"])
  }

  statement {
    sid       = "WriteTables"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.table_arn["approvals"], local.table_arn["incidents"], local.table_arn["jobs"], local.table_arn["cameras"]]
  }

  # Approvals append human_approval and status_change steps to the incident trace (D-07).
  statement {
    sid       = "AppendTrace"
    actions   = ["dynamodb:PutItem"]
    resources = [local.table_arn["agent-trace"]]
  }

  # Presigned URLs carry the Lambda's own permissions.
  statement {
    sid       = "PresignUploads"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.data_bucket}/incoming/*"]
  }

  statement {
    sid     = "PresignReads"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${local.data_bucket}/replay/*",
      "arn:aws:s3:::${local.data_bucket}/incoming/*",
      "arn:aws:s3:::${local.art_bucket}/evidence/*",
      "arn:aws:s3:::${local.art_bucket}/keyframes/*",
    ]
  }

  statement {
    sid       = "ConfirmApprovals"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.lifeguard.arn]
  }
}

module "api" {
  source      = "../../modules/lambda-fn"
  name        = "rw-api"
  source_dir  = "${local.repo_root}/lambdas/api"
  extra_files = local.api_shared
  timeout     = 15
  policy_json = data.aws_iam_policy_document.api.json
  env = merge(local.table_env, {
    RW_DATA_BUCKET         = local.data_bucket
    RW_ARTIFACTS_BUCKET    = local.art_bucket
    RW_TOPIC_LIFEGUARD_ARN = aws_sns_topic.lifeguard.arn
    RW_ALLOWED_ORIGIN      = var.dashboard_url
    RW_VERSION             = var.api_version
  })
}

data "aws_iam_policy_document" "ocean_poller" {
  statement {
    sid       = "OceanSnapshot"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter/rw/ocean/latest"]
  }
}

module "ocean_poller" {
  source      = "../../modules/lambda-fn"
  name        = "rw-ocean-poller"
  source_dir  = "${local.repo_root}/lambdas/ocean_poller"
  timeout     = 30
  policy_json = data.aws_iam_policy_document.ocean_poller.json
  env = {
    RW_NOAA_STATION_ID = var.noaa_station_id
    RW_NWS_ZONE_ID     = var.nws_zone_id
    RW_NWS_USER_AGENT  = var.nws_user_agent
  }
}

data "aws_iam_policy_document" "scheduler_fn" {
  statement {
    sid       = "ScaleWorker"
    actions   = ["autoscaling:SetDesiredCapacity", "autoscaling:UpdateAutoScalingGroup"]
    resources = [local.asg_arn]
  }

  statement {
    sid       = "DescribeWorker"
    actions   = ["autoscaling:DescribeAutoScalingGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "ExportTables"
    actions   = ["dynamodb:Scan"]
    resources = [local.table_arn["incidents"], local.table_arn["agent-trace"], local.table_arn["approvals"]]
  }

  statement {
    sid       = "WriteExports"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.art_bucket}/traces/*"]
  }
}

module "scheduler_fn" {
  source      = "../../modules/lambda-fn"
  name        = "rw-scheduler"
  source_dir  = "${local.repo_root}/lambdas/scheduler"
  timeout     = 120
  policy_json = data.aws_iam_policy_document.scheduler_fn.json
  env = merge(local.table_env, {
    RW_WORKER_ASG       = "rw-worker-asg"
    RW_ARTIFACTS_BUCKET = local.art_bucket
  })
}

data "aws_iam_policy_document" "kill_switch" {
  statement {
    sid       = "ScaleWorker"
    actions   = ["autoscaling:SetDesiredCapacity", "autoscaling:UpdateAutoScalingGroup"]
    resources = [local.asg_arn]
  }

  statement {
    sid       = "DescribeWorker"
    actions   = ["autoscaling:DescribeAutoScalingGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "TellTheTeam"
    actions   = ["sns:Publish"]
    resources = [local.ops_topic]
  }
}

module "kill_switch" {
  source      = "../../modules/lambda-fn"
  name        = "rw-kill-switch"
  source_dir  = "${local.repo_root}/lambdas/kill_switch"
  timeout     = 30
  policy_json = data.aws_iam_policy_document.kill_switch.json
  env = {
    RW_WORKER_ASG    = "rw-worker-asg"
    RW_OPS_TOPIC_ARN = local.ops_topic
  }
}

# The 80% budget notice reaches the kill switch (topic from the bootstrap stack).
resource "aws_lambda_permission" "kill_switch_sns" {
  statement_id  = "AllowRwKillSwitchTopic"
  action        = "lambda:InvokeFunction"
  function_name = module.kill_switch.name
  principal     = "sns.amazonaws.com"
  source_arn    = local.kill_topic
}

resource "aws_sns_topic_subscription" "kill_switch" {
  topic_arn  = local.kill_topic
  protocol   = "lambda"
  endpoint   = module.kill_switch.arn
  depends_on = [aws_lambda_permission.kill_switch_sns]
}

# ---------------------------------------------------------------- Schedules (Asia/Kolkata)

data "aws_iam_policy_document" "schedules_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "schedules" {
  name               = "rw-schedules-role"
  assume_role_policy = data.aws_iam_policy_document.schedules_assume.json
}

data "aws_iam_policy_document" "schedules" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.scheduler_fn.arn, module.ocean_poller.arn]
  }
}

resource "aws_iam_role_policy" "schedules" {
  name   = "rw-schedules-invoke"
  role   = aws_iam_role.schedules.id
  policy = data.aws_iam_policy_document.schedules.json
}

locals {
  schedules = {
    "rw-sleep-nightly"  = { expr = "cron(0 1 * * ? *)", target = module.scheduler_fn.arn, input = { action = "sleep" }, enabled = !var.judging_mode }
    "rw-export-nightly" = { expr = "cron(15 1 * * ? *)", target = module.scheduler_fn.arn, input = { action = "export" }, enabled = true }
    "rw-ocean-poll"     = { expr = "rate(30 minutes)", target = module.ocean_poller.arn, input = {}, enabled = true }
  }
}

resource "aws_scheduler_schedule" "this" {
  #checkov:skip=CKV_AWS_297:Payloads are {"action":"sleep"} style commands, nothing sensitive; a CMK costs monthly
  for_each                     = local.schedules
  name                         = each.key
  schedule_expression          = each.value.expr
  schedule_expression_timezone = "Asia/Kolkata"
  state                        = each.value.enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.target
    role_arn = aws_iam_role.schedules.arn
    input    = jsonencode(each.value.input)
  }
}

# ---------------------------------------------------------------- HTTP API (rw-http-api)

resource "aws_cloudwatch_log_group" "api_access" {
  #checkov:skip=CKV_AWS_158:Access logs carry no secrets; a CMK costs monthly
  #checkov:skip=CKV_AWS_338:7-day retention by design (north star 1 and 14.1)
  name              = "/aws/apigateway/rw-http-api"
  retention_in_days = 7
}

resource "aws_apigatewayv2_api" "http" {
  name          = "rw-http-api"
  protocol_type = "HTTP"
  description   = "RipWatch dashboard backend, served under /api/* through CloudFront"
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "rw-cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = module.cognito.issuer_url
    audience = [module.cognito.client_id]
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

locals {
  # sprint-1.md D-07; every route needs a Cognito JWT except health.
  routes = {
    "GET /api/health"                            = false
    "GET /api/cameras"                           = true
    "GET /api/cameras/{camera_id}/detections"    = true
    "GET /api/incidents"                         = true
    "GET /api/incidents/{incident_id}"           = true
    "GET /api/incidents/{incident_id}/trace"     = true
    "POST /api/incidents/{incident_id}/approval" = true
    "POST /api/uploads"                          = true
    "GET /api/jobs/{job_id}"                     = true
    "GET /api/media"                             = true
  }
}

resource "aws_apigatewayv2_route" "this" {
  #checkov:skip=CKV_AWS_309:Every route uses the Cognito JWT authorizer except GET /api/health, which is public by design
  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = each.value ? "JWT" : "NONE"
  authorizer_id      = each.value ? aws_apigatewayv2_authorizer.jwt.id : null
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 20
    throttling_burst_limit = 50
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      route     = "$context.routeKey"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
      user      = "$context.authorizer.claims.email"
      error     = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowRwHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = module.api.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
