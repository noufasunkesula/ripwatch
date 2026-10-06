# Edge (sprint-1.md N-09, north star 12): CloudFront in front of the dashboard bucket and the API.
# Reads the frontend bucket and rw-http-api by name, so data and serverless must be applied first.

data "aws_caller_identity" "current" {}

data "aws_s3_bucket" "frontend" {
  bucket = "rw-frontend-${data.aws_caller_identity.current.account_id}"
}

data "aws_apigatewayv2_apis" "http" {
  name          = "rw-http-api"
  protocol_type = "HTTP"
}

data "aws_apigatewayv2_api" "http" {
  api_id = one(data.aws_apigatewayv2_apis.http.ids)
}

module "site" {
  source                      = "../../modules/cloudfront-site"
  name                        = "rw-cdn"
  bucket_regional_domain_name = data.aws_s3_bucket.frontend.bucket_regional_domain_name
  api_origin_domain           = replace(data.aws_apigatewayv2_api.http.api_endpoint, "https://", "")
}

# The whole frontend bucket policy lives here (the data stack sets manage_policy = false):
# TLS only, and reads only through this distribution.
data "aws_iam_policy_document" "frontend" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [data.aws_s3_bucket.frontend.arn, "${data.aws_s3_bucket.frontend.arn}/*"]

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
    sid       = "CloudFrontReadsDashboard"
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [module.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = data.aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend.json
}
