output "id" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.id
}

output "arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}

output "tls_policy_json" {
  description = "TLS-only policy (plus extra_policy_json), for a stack that owns the policy (manage_policy = false)."
  value       = data.aws_iam_policy_document.this.json
}

output "bucket_regional_domain_name" {
  description = "Regional domain name (CloudFront origin)."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}
