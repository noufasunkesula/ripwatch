locals {
  tags = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_18:Access logs need another bucket and cost; CloudTrail records management events
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles storage cost; data is reproducible
  #checkov:skip=CKV_AWS_145:SSE-S3 by design (north star 15); KMS adds per-request cost
  #checkov:skip=CKV2_AWS_62:Event notifications are set by the stacks that need them (rw-data to rw-jobs)
  #checkov:skip=CKV_AWS_21:Versioning is a per-bucket input; on for state and artifacts, off for scratch data
  #checkov:skip=CKV2_AWS_61:Lifecycle rules are a per-bucket input
  bucket        = var.name
  force_destroy = var.force_destroy
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Only created when wanted: a bucket that never had versioning stays unversioned.
resource "aws_s3_bucket_versioning" "this" {
  count  = var.versioning ? 1 : 0
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  #checkov:skip=CKV_AWS_300:Every rule below aborts incomplete uploads after 7 days; checkov cannot read dynamic rule blocks
  count  = length(var.lifecycle_rules) > 0 ? 1 : 0
  bucket = aws_s3_bucket.this.id

  dynamic "rule" {
    for_each = var.lifecycle_rules
    content {
      id     = rule.value.id
      status = "Enabled"

      filter {
        prefix = rule.value.prefix
      }

      dynamic "expiration" {
        for_each = rule.value.expiration_days == null ? [] : [rule.value.expiration_days]
        content {
          days = expiration.value
        }
      }

      dynamic "noncurrent_version_expiration" {
        for_each = rule.value.noncurrent_expiration_days == null ? [] : [rule.value.noncurrent_expiration_days]
        content {
          noncurrent_days = noncurrent_version_expiration.value
        }
      }

      abort_incomplete_multipart_upload {
        days_after_initiation = 7
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_cors_configuration" "this" {
  count  = length(var.cors_rules) > 0 ? 1 : 0
  bucket = aws_s3_bucket.this.id

  dynamic "cors_rule" {
    for_each = var.cors_rules
    content {
      allowed_methods = cors_rule.value.allowed_methods
      allowed_origins = cors_rule.value.allowed_origins
      allowed_headers = cors_rule.value.allowed_headers
      expose_headers  = cors_rule.value.expose_headers
      max_age_seconds = cors_rule.value.max_age_seconds
    }
  }
}

data "aws_iam_policy_document" "this" {
  source_policy_documents = var.extra_policy_json == null ? [] : [var.extra_policy_json]

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]

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
}

resource "aws_s3_bucket_policy" "this" {
  count  = var.manage_policy ? 1 : 0
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.this.json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
