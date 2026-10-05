# Compute (sprint-1.md N-09, north star 8): the worker role, launch template, ASG, logs, deploy.
# Reads network resources by name, so network and data must be applied first (section 0.3).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_vpc" "rw" {
  tags = { Name = "rw-vpc" }
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.rw.id]
  }

  tags = { Name = "rw-public-*" }
}

data "aws_security_group" "worker" {
  name   = "rw-worker-sg"
  vpc_id = data.aws_vpc.rw.id
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  data_arn   = "arn:aws:s3:::rw-data-${local.account_id}"
  art_bucket = "rw-artifacts-${local.account_id}"
  art_arn    = "arn:aws:s3:::${local.art_bucket}"
  table_arns = [for t in ["rw-cameras", "rw-jobs", "rw-detections", "rw-incidents", "rw-agent-trace", "rw-approvals"] :
  "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${t}"]
  queue_arn = "arn:aws:sqs:${local.region}:${local.account_id}"
  services  = ["rw-camera-sim", "rw-ingest", "rw-vision", "rw-mcp-tools", "rw-agent", "deploy"]
}

# ---------------------------------------------------------------- Worker identity

data "aws_iam_policy_document" "worker_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "rw-worker-role"
  assume_role_policy = data.aws_iam_policy_document.worker_assume.json
}

resource "aws_iam_role_policy_attachment" "worker_managed" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
  ])
  role       = aws_iam_role.worker.name
  policy_arn = each.value
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket"]
    resources = [local.data_arn, local.art_arn]
  }

  statement {
    sid       = "ReadData"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_arn}/*"]
  }

  # Camera simulation copies replay/ clips into incoming/.
  statement {
    sid       = "ReplayToIncoming"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_arn}/incoming/*"]
  }

  statement {
    sid     = "WriteArtifacts"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [for p in ["evidence", "keyframes", "benchmarks", "eval", "traces", "results"] :
    "${local.art_arn}/${p}/*"]
  }

  statement {
    sid       = "ReadReleasesAndModels"
    actions   = ["s3:GetObject"]
    resources = ["${local.art_arn}/releases/*", "${local.art_arn}/models/*"]
  }

  statement {
    sid = "Tables"
    actions = [
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:ConditionCheckItem",
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
    ]
    resources = concat(local.table_arns, [for t in local.table_arns : "${t}/index/*"])
  }

  statement {
    sid       = "ConsumeJobs"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
    resources = ["${local.queue_arn}:rw-jobs"]
  }

  statement {
    sid       = "Candidates"
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
    resources = ["${local.queue_arn}:rw-candidates"]
  }

  statement {
    sid       = "LifeguardAlerts"
    actions   = ["sns:Publish"]
    resources = ["arn:aws:sns:${local.region}:${local.account_id}:rw-lifeguard-alerts"]
  }

  statement {
    sid     = "Bedrock"
    actions = ["bedrock:InvokeModel", "bedrock:Converse"]
    resources = [
      "arn:aws:bedrock:${local.region}::foundation-model/${var.bedrock_model_id}",
      "arn:aws:bedrock:*:${local.account_id}:inference-profile/*${var.bedrock_model_id}",
      "arn:aws:bedrock:*::foundation-model/${var.bedrock_model_id}",
    ]
  }

  # Read only: the worker never writes config (ssm:PutParameter is not granted).
  statement {
    sid       = "ReadConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter/rw/*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "rw-worker-policy"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_instance_profile" "worker" {
  name = "rw-worker-role"
  role = aws_iam_role.worker.name
}

# ---------------------------------------------------------------- Logs, deploy document, agent config

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:Logs carry no secrets; a CMK costs monthly
  #checkov:skip=CKV_AWS_338:7-day retention by design (north star 1 and 14.1)
  for_each          = toset(local.services)
  name              = "/rw/worker/${each.value}"
  retention_in_days = 7
}

resource "aws_ssm_document" "deploy" {
  name            = "rw-deploy"
  document_type   = "Command"
  document_format = "YAML"
  content         = file("${path.module}/../../../deploy/ssm/rw-deploy.yaml")
}

resource "aws_ssm_parameter" "cloudwatch_agent" {
  #checkov:skip=CKV2_AWS_34:Agent config, no secrets
  name  = "/rw/cloudwatch-agent/config"
  type  = "String"
  tier  = "Standard"
  value = file("${path.module}/../../../deploy/cloudwatch-agent.json")
}

# ---------------------------------------------------------------- Worker

locals {
  template_vars = {
    region           = local.region
    artifacts_bucket = local.art_bucket
    runtime          = var.runtime
    bedrock_model_id = var.bedrock_model_id
  }
}

module "worker" {
  source                = "../../modules/worker-asg"
  ami_id                = var.cool_ami_id
  instance_type         = var.instance_type
  subnet_ids            = data.aws_subnets.public.ids
  security_group_id     = data.aws_security_group.worker.id
  instance_profile_name = aws_iam_instance_profile.worker.name

  user_data = templatefile("${path.module}/../../../deploy/user_data.sh", merge(local.template_vars, {
    rw_env = templatefile("${path.module}/../../../deploy/rw.env.tpl", local.template_vars)
  }))

  depends_on = [aws_ssm_parameter.cloudwatch_agent, aws_cloudwatch_log_group.worker]
}
