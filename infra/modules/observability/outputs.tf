output "dashboard_names" {
  description = "rw-ops, rw-agent, rw-benchmark."
  value       = [for d in aws_cloudwatch_dashboard.this : d.dashboard_name]
}

output "alarm_names" {
  description = "All alarm names (6 with two DLQs, under the 10 free alarms)."
  value = concat(
    [aws_cloudwatch_metric_alarm.fps_low.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.dlq_not_empty : a.alarm_name],
    [
      aws_cloudwatch_metric_alarm.agent_fallback_high.alarm_name,
      aws_cloudwatch_metric_alarm.api_errors.alarm_name,
      aws_cloudwatch_metric_alarm.worker_running_long.alarm_name,
    ],
  )
}
