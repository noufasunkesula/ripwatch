output "name" {
  description = "Function name."
  value       = aws_lambda_function.this.function_name
}

output "arn" {
  description = "Function ARN."
  value       = aws_lambda_function.this.arn
}

output "invoke_arn" {
  description = "ARN for API Gateway integrations."
  value       = aws_lambda_function.this.invoke_arn
}

output "role_arn" {
  description = "Execution role ARN."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Execution role name."
  value       = aws_iam_role.this.name
}
