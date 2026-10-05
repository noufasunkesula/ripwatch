# Bootstrap (sprint-1.md N-06, north star section 4): state bucket, CI identity, cost guards.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  repo       = "${var.github_owner}/${var.github_repo}"
}

# ---------------------------------------------------------------- Terraform state bucket

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket; CloudTrail covers who changed state
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles cost; versioning protects state
  #checkov:skip=CKV_AWS_145:SSE-S3 by design (north star 15); KMS adds per-request cost
  #checkov:skip=CKV2_AWS_62:No consumer for event notifications on the state bucket
  bucket = "rw-tfstate-${local.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyStateDeleteExceptRoot"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.state.arn}/*.tfstate"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# ---------------------------------------------------------------- GitHub Actions OIDC

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "gha_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${local.repo}:ref:refs/heads/main",
        "repo:${local.repo}:pull_request",
      ]
    }
  }
}

resource "aws_iam_role" "gha" {
  name                 = "rw-gha"
  description          = "GitHub Actions for ${local.repo}: plan, apply, release, deploy"
  assume_role_policy   = data.aws_iam_policy_document.gha_trust.json
  max_session_duration = 3600
}

# TODO(sprint-3): replace PowerUserAccess with a policy listing only the services RipWatch uses.
resource "aws_iam_role_policy_attachment" "gha_power_user" {
  role       = aws_iam_role.gha.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "gha_iam" {
  # TODO(sprint-3): narrow to the exact rw-* roles and policies each stack creates.
  statement {
    sid = "ManageRipWatchIam"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:CreateRole",
      "iam:DeleteInstanceProfile",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetInstanceProfile",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListPolicyVersions",
      "iam:ListRolePolicies",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagPolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/rw-*",
      "arn:aws:iam::${local.account_id}:policy/rw-*",
      "arn:aws:iam::${local.account_id}:instance-profile/rw-*",
    ]
  }
}

resource "aws_iam_role_policy" "gha_iam" {
  name   = "rw-gha-iam"
  role   = aws_iam_role.gha.id
  policy = data.aws_iam_policy_document.gha_iam.json
}

# ---------------------------------------------------------------- Alert topics

data "aws_iam_policy_document" "ops_alerts" {
  statement {
    sid       = "BudgetsAndAlarmsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic" "ops_alerts" {
  #checkov:skip=CKV_AWS_26:Budgets cannot publish to a topic encrypted with the AWS managed key; a CMK costs monthly. Alert text is not sensitive
  name = "rw-ops-alerts"
}

resource "aws_sns_topic_policy" "ops_alerts" {
  arn    = aws_sns_topic.ops_alerts.arn
  policy = data.aws_iam_policy_document.ops_alerts.json
}

resource "aws_sns_topic_subscription" "ops_alerts_email" {
  for_each  = toset(var.team_emails)
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

data "aws_iam_policy_document" "kill_switch" {
  statement {
    sid       = "BudgetsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.kill_switch.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic" "kill_switch" {
  #checkov:skip=CKV_AWS_26:Budgets cannot publish to a topic encrypted with the AWS managed key; a CMK costs monthly. Message is a budget notice
  name = "rw-kill-switch"
}

resource "aws_sns_topic_policy" "kill_switch" {
  arn    = aws_sns_topic.kill_switch.arn
  policy = data.aws_iam_policy_document.kill_switch.json
}

# ---------------------------------------------------------------- Budgets and anomaly detection

resource "aws_budgets_budget" "monthly" {
  name         = "rw-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [25, 50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.team_emails
      subscriber_sns_topic_arns  = [aws_sns_topic.ops_alerts.arn]
    }
  }

  # 80% feeds the kill switch topic only (Decision Log 2026-10-01).
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.team_emails
    subscriber_sns_topic_arns  = [aws_sns_topic.kill_switch.arn]
  }

  depends_on = [aws_sns_topic_policy.ops_alerts, aws_sns_topic_policy.kill_switch]
}

resource "aws_budgets_budget" "bedrock" {
  name         = "rw-bedrock"
  budget_type  = "COST"
  limit_amount = tostring(var.bedrock_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Bedrock"]
  }

  dynamic "notification" {
    for_each = [80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.team_emails
    }
  }
}

resource "aws_ce_anomaly_monitor" "services" {
  name              = "rw-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "daily" {
  name             = "rw-daily-anomalies"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services.arn]

  dynamic "subscriber" {
    for_each = toset(var.team_emails)
    content {
      type    = "EMAIL"
      address = subscriber.value
    }
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_threshold_usd)]
    }
  }
}
