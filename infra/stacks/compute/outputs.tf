output "asg_name" {
  description = "rw-worker-asg (RW_WORKER_ASG for the scheduler and kill-switch Lambdas)."
  value       = module.worker.asg_name
}

output "launch_template_id" {
  description = "rw-worker-lt."
  value       = module.worker.launch_template_id
}

output "worker_role_arn" {
  description = "rw-worker-role."
  value       = aws_iam_role.worker.arn
}

output "deploy_document" {
  description = "SSM document run by make deploy and CI."
  value       = aws_ssm_document.deploy.name
}
