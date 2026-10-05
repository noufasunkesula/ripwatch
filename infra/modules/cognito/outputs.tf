output "user_pool_id" {
  description = "User pool ID."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "User pool ARN."
  value       = aws_cognito_user_pool.this.arn
}

output "client_id" {
  description = "Dashboard app client ID (public, no secret)."
  value       = aws_cognito_user_pool_client.spa.id
}

output "issuer_url" {
  description = "JWT issuer for the HTTP API authorizer."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "domain" {
  description = "Hosted UI domain prefix (https://<domain>.auth.<region>.amazoncognito.com)."
  value       = aws_cognito_user_pool_domain.this.domain
}
