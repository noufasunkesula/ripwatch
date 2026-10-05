output "api_endpoint" {
  description = "HTTP API base URL."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "api_domain" {
  description = "HTTP API domain without scheme (CloudFront origin for /api/*)."
  value       = replace(aws_apigatewayv2_api.http.api_endpoint, "https://", "")
}

output "user_pool_id" {
  description = "Cognito user pool rw-users."
  value       = module.cognito.user_pool_id
}

output "client_id" {
  description = "Dashboard app client."
  value       = module.cognito.client_id
}

output "cognito_domain" {
  description = "Hosted UI domain prefix."
  value       = module.cognito.domain
}

output "lifeguard_topic_arn" {
  description = "RW_TOPIC_LIFEGUARD_ARN."
  value       = aws_sns_topic.lifeguard.arn
}

output "lambda_names" {
  description = "All four functions."
  value       = [module.api.name, module.ocean_poller.name, module.scheduler_fn.name, module.kill_switch.name]
}
