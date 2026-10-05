data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_origin_access_control" "this" {
  name                              = "${var.name}-oac"
  description                       = "CloudFront to the rw-frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "this" {
  #checkov:skip=CKV_AWS_68:No WAF by design (north star 1); API Gateway throttles and Cognito auth protect the API
  #checkov:skip=CKV2_AWS_47:No WAF by design (north star 1)
  #checkov:skip=CKV_AWS_86:Access logs need a log bucket and cost; API logs are in CloudWatch
  #checkov:skip=CKV_AWS_310:One S3 origin and one API origin; failover adds nothing for a demo dashboard
  #checkov:skip=CKV_AWS_174:Default CloudFront certificate (no custom domain, north star 22) cannot set a minimum TLS version
  #checkov:skip=CKV2_AWS_42:No custom domain by design (north star 22)
  #checkov:skip=CKV_AWS_374:Geo restriction not needed; access is gated by Cognito
  enabled             = true
  comment             = var.name
  default_root_object = "index.html"
  price_class         = var.price_class
  http_version        = "http2and3"
  is_ipv6_enabled     = true
  tags                = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)

  origin {
    origin_id                = "frontend"
    domain_name              = var.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.this.id
  }

  origin {
    origin_id   = "api"
    domain_name = var.api_origin_domain

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "frontend"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  # SPA fallback: client-side routes load index.html.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
