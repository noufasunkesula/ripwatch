# Data (sprint-1.md N-08, north star 7): buckets, tables, queues, config, audit.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  trail_arn  = "arn:aws:cloudtrail:${local.region}:${local.account_id}:trail/rw-trail"
}

# ---------------------------------------------------------------- Buckets

module "data_bucket" {
  source = "../../modules/s3-bucket"
  name   = "rw-data-${local.account_id}"

  lifecycle_rules = [
    { id = "expire-uploads", prefix = "incoming/", expiration_days = 7 },
  ]

  # Browser uploads with presigned URLs from the dashboard.
  cors_rules = [
    { allowed_methods = ["POST", "PUT"], allowed_origins = [var.allowed_origin] },
  ]
}

data "aws_iam_policy_document" "artifacts_writers" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = ["arn:aws:s3:::rw-artifacts-${local.account_id}"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::rw-artifacts-${local.account_id}/cloudtrail/AWSLogs/${local.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "BedrockInvocationLogs"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::rw-artifacts-${local.account_id}/bedrock-logs/*"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${local.region}:${local.account_id}:*"]
    }
  }
}

module "artifacts_bucket" {
  source     = "../../modules/s3-bucket"
  name       = "rw-artifacts-${local.account_id}"
  versioning = true

  lifecycle_rules = [
    { id = "old-versions", noncurrent_expiration_days = 14 },
    { id = "keyframes", prefix = "keyframes/", expiration_days = 7 },
    { id = "cloudtrail", prefix = "cloudtrail/", expiration_days = 30 },
  ]

  extra_policy_json = data.aws_iam_policy_document.artifacts_writers.json
}

# The edge stack owns this bucket's whole policy (TLS-only deny plus CloudFront OAC read).
module "frontend_bucket" {
  source        = "../../modules/s3-bucket"
  name          = "rw-frontend-${local.account_id}"
  manage_policy = false
}

# ---------------------------------------------------------------- Tables (sprint-1.md N-08)

module "table_cameras" {
  source     = "../../modules/dynamodb-table"
  name       = "rw-cameras"
  hash_key   = "camera_id"
  attributes = [{ name = "camera_id", type = "S" }]
}

module "table_jobs" {
  source        = "../../modules/dynamodb-table"
  name          = "rw-jobs"
  hash_key      = "job_id"
  ttl_attribute = "expires_at"
  attributes = [
    { name = "job_id", type = "S" },
    { name = "camera_id", type = "S" },
    { name = "created_at", type = "S" },
  ]
  gsis = [{ name = "camera-index", hash_key = "camera_id", range_key = "created_at" }]
}

# ts_result = "<start_ts %Y-%m-%dT%H:%M:%S.%fZ>#<result_id>" (Decision Log 2026-10-03).
module "table_detections" {
  source        = "../../modules/dynamodb-table"
  name          = "rw-detections"
  hash_key      = "camera_id"
  range_key     = "ts_result"
  ttl_attribute = "expires_at"
  attributes = [
    { name = "camera_id", type = "S" },
    { name = "ts_result", type = "S" },
  ]
}

module "table_incidents" {
  source   = "../../modules/dynamodb-table"
  name     = "rw-incidents"
  hash_key = "incident_id"
  attributes = [
    { name = "incident_id", type = "S" },
    { name = "status", type = "S" },
    { name = "created_at", type = "S" },
  ]
  gsis = [{ name = "status-index", hash_key = "status", range_key = "created_at" }]
}

module "table_trace" {
  source    = "../../modules/dynamodb-table"
  name      = "rw-agent-trace"
  hash_key  = "trace_key"
  range_key = "step"
  attributes = [
    { name = "trace_key", type = "S" },
    { name = "step", type = "N" },
  ]
}

module "table_approvals" {
  source    = "../../modules/dynamodb-table"
  name      = "rw-approvals"
  hash_key  = "incident_id"
  range_key = "ts"
  attributes = [
    { name = "incident_id", type = "S" },
    { name = "ts", type = "S" },
  ]
}

# ---------------------------------------------------------------- Queues and upload events

module "queue_jobs" {
  source               = "../../modules/sqs-queue"
  name                 = "rw-jobs"
  visibility_timeout_s = 300
}

module "queue_candidates" {
  source               = "../../modules/sqs-queue"
  name                 = "rw-candidates"
  visibility_timeout_s = 120
}

data "aws_iam_policy_document" "jobs_from_s3" {
  statement {
    sid       = "S3UploadEvents"
    actions   = ["sqs:SendMessage"]
    resources = [module.queue_jobs.arn]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [module.data_bucket.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = module.queue_jobs.url
  policy    = data.aws_iam_policy_document.jobs_from_s3.json
}

resource "aws_s3_bucket_notification" "uploads" {
  bucket = module.data_bucket.id

  dynamic "queue" {
    for_each = [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".zip"]
    content {
      id            = "incoming${replace(queue.value, ".", "-")}"
      queue_arn     = module.queue_jobs.arn
      events        = ["s3:ObjectCreated:*"]
      filter_prefix = "incoming/"
      filter_suffix = queue.value
    }
  }

  depends_on = [aws_sqs_queue_policy.jobs]
}

# ---------------------------------------------------------------- Config (SSM, standard tier)
# sprint-1.md references "section 9.6" for this list, which does not exist yet. These come from
# north star 7.3 plus every /rw/ parameter sprint-1.md mentions (Decision Log 2026-10-05).

locals {
  ssm_managed = {
    "/rw/bedrock/model_id"           = var.bedrock_model_id
    "/rw/vision/frame_width"         = "640"
    "/rw/vision/flow_fps"            = "5"
    "/rw/vision/flow_scale"          = "0.5"
    "/rw/vision/rip_threshold"       = "0.70"
    "/rw/vision/uncertain_threshold" = "0.40"
    "/rw/vision/min_rip_area_px"     = "400"
    "/rw/agent/max_tool_calls"       = "6"
    "/rw/agent/cooldown_s"           = "60"
    "/rw/noaa/station_id"            = var.noaa_station_id
    "/rw/nws/zone_id"                = var.nws_zone_id
    "/rw/cool/enabled"               = "false"
  }

  # Written at runtime by CI (release), rw-ocean-poller (ocean) or D-05 (risk rules, sprint-1.md
  # 9.5 pending); Terraform only creates them.
  ssm_runtime = {
    "/rw/release"         = "none"
    "/rw/ocean/latest"    = "{}"
    "/rw/risk/thresholds" = "{}"
  }
}

resource "aws_ssm_parameter" "managed" {
  #checkov:skip=CKV2_AWS_34:Plain config, never secrets; SecureString would need KMS calls on every read
  for_each = local.ssm_managed
  name     = each.key
  type     = "String"
  tier     = "Standard"
  value    = each.value
}

resource "aws_ssm_parameter" "runtime" {
  #checkov:skip=CKV2_AWS_34:Plain config, never secrets; SecureString would need KMS calls on every read
  for_each = local.ssm_runtime
  name     = each.key
  type     = "String"
  tier     = "Standard"
  value    = each.value

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------- Audit and Bedrock logs

resource "aws_cloudtrail" "this" {
  #checkov:skip=CKV_AWS_35:SSE-S3 on rw-artifacts by design; a CMK costs monthly
  #checkov:skip=CKV_AWS_67:Single region by design (sprint-1.md N-08); everything runs in us-east-1
  #checkov:skip=CKV_AWS_252:No SNS per log file; alarms come from CloudWatch
  #checkov:skip=CKV2_AWS_10:CloudWatch Logs delivery is billed; S3 copy is enough for audit
  name                          = "rw-trail"
  s3_bucket_name                = module.artifacts_bucket.id
  s3_key_prefix                 = "cloudtrail"
  include_global_service_events = true
  is_multi_region_trail         = false
  enable_log_file_validation    = true

  depends_on = [module.artifacts_bucket]
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  logging_config {
    text_data_delivery_enabled      = true
    image_data_delivery_enabled     = false
    embedding_data_delivery_enabled = false
    video_data_delivery_enabled     = false

    s3_config {
      bucket_name = module.artifacts_bucket.id
      key_prefix  = "bedrock-logs"
    }
  }

  depends_on = [module.artifacts_bucket]
}
