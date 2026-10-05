output "name" {
  description = "Queue name."
  value       = aws_sqs_queue.this.name
}

output "url" {
  description = "Queue URL."
  value       = aws_sqs_queue.this.id
}

output "arn" {
  description = "Queue ARN."
  value       = aws_sqs_queue.this.arn
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Dead-letter queue name (alarms)."
  value       = aws_sqs_queue.dlq.name
}
