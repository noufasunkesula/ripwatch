output "asg_name" {
  description = "Auto Scaling group name (rw-worker-asg)."
  value       = aws_autoscaling_group.this.name
}

output "launch_template_id" {
  description = "Launch template ID (rw-worker-lt)."
  value       = aws_launch_template.this.id
}
