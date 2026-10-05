variable "name" {
  description = "Queue name, e.g. rw-jobs. The DLQ is <name>-dlq."
  type        = string
}

variable "visibility_timeout_s" {
  description = "Seconds a received message stays hidden; longer than the slowest consumer."
  type        = number
}

variable "max_receive_count" {
  description = "Receives before a message moves to the DLQ."
  type        = number
  default     = 3
}

variable "retention_s" {
  description = "Main queue message retention in seconds (default 4 days)."
  type        = number
  default     = 345600
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
