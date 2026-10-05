# IAM for people (sprint-1.md N-08, north star 5). rw-noufa is hand-made and not managed here.
# No login profiles and no access keys: passwords and keys never land in state.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

resource "aws_iam_group" "developers" {
  name = "rw-developers"
}

resource "aws_iam_user" "dev" {
  #checkov:skip=CKV_AWS_273:Per-person IAM users with access keys instead of Identity Center (Decision Log 2026-10-01)
  for_each      = toset(var.iam_users)
  name          = each.value
  force_destroy = false
}

resource "aws_iam_user_group_membership" "dev" {
  for_each = aws_iam_user.dev
  user     = each.value.name
  groups   = [aws_iam_group.developers.name]
}

# ---------------------------------------------------------------- Console MFA

data "aws_iam_policy_document" "require_mfa" {
  statement {
    sid = "ManageOwnCredentials"
    actions = [
      "iam:ChangePassword",
      "iam:CreateAccessKey",
      "iam:DeleteAccessKey",
      "iam:GetAccessKeyLastUsed",
      "iam:GetLoginProfile",
      "iam:GetUser",
      "iam:ListAccessKeys",
      "iam:UpdateAccessKey",
      "iam:CreateVirtualMFADevice",
      "iam:DeleteVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:ListMFADevices",
      "iam:ResyncMFADevice",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:user/$${aws:username}",
      "arn:aws:iam::${local.account_id}:mfa/$${aws:username}",
    ]
  }

  statement {
    sid       = "SeeMfaAndPasswordRules"
    actions   = ["iam:ListVirtualMFADevices", "iam:GetAccountPasswordPolicy"]
    resources = ["*"]
  }

  # Console sessions carry aws:MultiFactorAuthPresent; access-key calls do not, so "Bool"
  # (not BoolIfExists) leaves CLI use with keys working.
  statement {
    sid    = "DenyConsoleWithoutMfa"
    effect = "Deny"
    not_actions = [
      "iam:ChangePassword",
      "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:GetUser",
      "iam:ListMFADevices",
      "iam:ListVirtualMFADevices",
      "iam:ResyncMFADevice",
      "iam:GetAccountPasswordPolicy",
      "sts:GetSessionToken",
    ]
    resources = ["*"]

    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["false"]
    }

    condition {
      test     = "Bool"
      variable = "aws:ViaAWSService"
      values   = ["false"]
    }
  }
}

resource "aws_iam_policy" "require_mfa" {
  name        = "rw-require-mfa-console"
  description = "Console users must enable MFA before doing anything else; CLI keys unaffected"
  policy      = data.aws_iam_policy_document.require_mfa.json
}

# ---------------------------------------------------------------- What developers can do

data "aws_iam_policy_document" "developers" {
  #checkov:skip=CKV_AWS_356:Only read-only Describe/List/Get actions AWS cannot scope by resource use "*"; everything else is scoped to rw-* ARNs
  statement {
    sid       = "ListRipWatchBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::rw-data-*", "arn:aws:s3:::rw-artifacts-*"]
  }

  statement {
    sid       = "ReadData"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::rw-data-*/*"]
  }

  statement {
    sid       = "ReadWriteArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::rw-artifacts-*/*"]
  }

  # CloudWatch metric and dashboard reads, log group listing and query results cannot be
  # scoped to a resource by AWS.
  statement {
    sid = "ReadMetricsDashboards"
    actions = [
      "cloudwatch:Describe*",
      "cloudwatch:Get*",
      "cloudwatch:List*",
      "logs:DescribeLogGroups",
      "logs:DescribeQueryDefinitions",
      "logs:GetQueryResults",
      "logs:StopQuery",
      "logs:StopLiveTail",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ReadLogs"
    actions = [
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:StartLiveTail",
      "logs:StartQuery",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid = "InvokeBedrock"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]
    resources = [
      "arn:aws:bedrock:${local.region}::foundation-model/*",
      "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
    ]
  }

  statement {
    sid       = "FindWorker"
    actions   = ["ec2:DescribeInstances", "autoscaling:DescribeAutoScalingGroups", "ssm:DescribeSessions"]
    resources = ["*"]
  }

  statement {
    sid       = "SessionToWorkerOnly"
    actions   = ["ssm:StartSession"]
    resources = ["arn:aws:ec2:${local.region}:${local.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/rw:role"
      values   = ["worker"]
    }
  }

  statement {
    sid       = "SessionDocument"
    actions   = ["ssm:StartSession"]
    resources = ["arn:aws:ssm:${local.region}::document/SSM-SessionManagerRunShell"]
  }

  statement {
    sid       = "OwnSessions"
    actions   = ["ssm:ResumeSession", "ssm:TerminateSession"]
    resources = ["arn:aws:ssm:*:*:session/$${aws:username}-*"]
  }

  statement {
    sid       = "ReadConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter/rw/*"]
  }

  statement {
    sid = "ReadTables"
    actions = [
      "dynamodb:BatchGetItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      "arn:aws:dynamodb:${local.region}:${local.account_id}:table/rw-*",
      "arn:aws:dynamodb:${local.region}:${local.account_id}:table/rw-*/index/*",
    ]
  }

  statement {
    sid       = "ListTables"
    actions   = ["dynamodb:ListTables"]
    resources = ["*"]
  }

  # Guardrail (north star 5): one wrong command cannot delete raw data or Terraform state.
  statement {
    sid       = "DenyDeleteRawAndState"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["arn:aws:s3:::rw-data-*/raw/*", "arn:aws:s3:::rw-tfstate-*", "arn:aws:s3:::rw-tfstate-*/*"]
  }
}

resource "aws_iam_policy" "developers" {
  name        = "rw-developers"
  description = "RipWatch developers: read data, write artifacts, logs, Bedrock, SSM to the worker"
  policy      = data.aws_iam_policy_document.developers.json
}

resource "aws_iam_group_policy_attachment" "developers" {
  for_each = {
    developers  = aws_iam_policy.developers.arn
    require_mfa = aws_iam_policy.require_mfa.arn
  }
  group      = aws_iam_group.developers.name
  policy_arn = each.value
}
