output "domain_name" {
  description = "Distribution domain (the dashboard URL, also the CORS origin and Cognito callback)."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "distribution_id" {
  description = "Distribution ID (cache invalidation)."
  value       = aws_cloudfront_distribution.this.id
}

output "arn" {
  description = "Distribution ARN (rw-frontend bucket policy condition)."
  value       = aws_cloudfront_distribution.this.arn
}
