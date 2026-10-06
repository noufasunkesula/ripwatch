locals {
  tags = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)

  # Every file under source_dir (minus bytecode) plus extra_files, as zip path => source path.
  package_files = merge(
    {
      for f in fileset(var.source_dir, "**") : f => "${var.source_dir}/${f}"
      if !strcontains(f, "__pycache__") && !endswith(f, ".pyc")
    },
    var.extra_files,
  )
}

data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.root}/.build/${var.name}.zip"

  dynamic "source" {
    for_each = local.package_files
    content {
      filename = source.key
      content  = file(source.value)
    }
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "this" {
  count  = var.policy_json == null ? 0 : 1
  name   = "${var.name}-policy"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

# Created before the function so Lambda never makes one with infinite retention.
resource "aws_cloudwatch_log_group" "this" {
  #checkov:skip=CKV_AWS_158:Logs carry no secrets; a CMK costs monthly
  #checkov:skip=CKV_AWS_338:7-day retention by design (north star 1 and 14.1)
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_50:X-Ray off to stay inside free allowances; traces live in rw-agent-trace
  #checkov:skip=CKV_AWS_115:New accounts have a concurrency quota of 10; reserving would starve other functions
  #checkov:skip=CKV_AWS_116:Callers are API Gateway (sync), SNS and EventBridge, which retry themselves
  #checkov:skip=CKV_AWS_117:No VPC by design (no NAT, north star 6); functions only call AWS APIs and NOAA/NWS
  #checkov:skip=CKV_AWS_173:Env vars hold names and ARNs, never secrets; default Lambda encryption applies
  #checkov:skip=CKV_AWS_272:Code signing adds a signer profile per deploy; CI deploys from main only
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = var.handler
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  timeout          = var.timeout
  memory_size      = var.memory
  tags             = local.tags

  dynamic "environment" {
    for_each = length(var.env) > 0 ? [var.env] : []
    content {
      variables = environment.value
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy_attachment.basic]
}
