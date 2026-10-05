variable "namespace" {
  description = "Custom metric namespace (RW_METRIC_NAMESPACE)."
  type        = string
  default     = "RipWatch"
}

variable "queue_names" {
  description = "Main queues shown on rw-ops (rw-jobs, rw-candidates)."
  type        = list(string)
}

variable "dlq_names" {
  description = "Dead-letter queues; each gets a not-empty alarm."
  type        = list(string)
}

variable "asg_name" {
  description = "Worker Auto Scaling group (rw-worker-asg)."
  type        = string
}

variable "api_lambda_name" {
  description = "rw-api function name (error alarm)."
  type        = string
}

variable "lambda_names" {
  description = "All RipWatch Lambda function names (rw-ops widgets)."
  type        = list(string)
}

variable "log_group_names" {
  description = "Log groups the saved Logs Insights queries search."
  type        = list(string)
}

variable "ops_topic_arn" {
  description = "rw-ops-alerts topic ARN (alarm actions)."
  type        = string
}

variable "worker_runtime_alarm_enabled" {
  description = "Alarm when the worker runs more than 14 h straight. Build phase only; disable during judging."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
