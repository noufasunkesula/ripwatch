output "group_name" {
  description = "Developer group."
  value       = aws_iam_group.developers.name
}

output "user_names" {
  description = "Managed developer users. Each creates their own access key in the console."
  value       = [for u in aws_iam_user.dev : u.name]
}

output "developers_policy_arn" {
  description = "rw-developers policy."
  value       = aws_iam_policy.developers.arn
}
