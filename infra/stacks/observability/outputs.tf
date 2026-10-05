output "dashboard_names" {
  description = "rw-ops, rw-agent, rw-benchmark."
  value       = module.observability.dashboard_names
}

output "alarm_names" {
  description = "Alarms sending to rw-ops-alerts."
  value       = module.observability.alarm_names
}
