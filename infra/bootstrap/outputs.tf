output "state_bucket" {
  description = "Terraform state bucket for every other stack (infra/backend.hcl)."
  value       = aws_s3_bucket.state.bucket
}

output "gha_role_arn" {
  description = "Role GitHub Actions assumes through OIDC."
  value       = aws_iam_role.gha.arn
}

output "ops_alerts_topic_arn" {
  description = "SNS rw-ops-alerts (alarms and budget notices)."
  value       = aws_sns_topic.ops_alerts.arn
}

output "kill_switch_topic_arn" {
  description = "SNS rw-kill-switch (80% budget notice; the rw-kill-switch Lambda subscribes)."
  value       = aws_sns_topic.kill_switch.arn
}
