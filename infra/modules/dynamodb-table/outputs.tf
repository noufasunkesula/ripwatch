output "name" {
  description = "Table name."
  value       = aws_dynamodb_table.this.name
}

output "arn" {
  description = "Table ARN (indexes: \"<arn>/index/*\")."
  value       = aws_dynamodb_table.this.arn
}

output "stream_arn" {
  description = "Always null: streams are not used."
  value       = null
}
