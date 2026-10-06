variable "name" {
  description = "Function name, e.g. rw-api."
  type        = string
}

variable "source_dir" {
  description = "Folder zipped as the deployment package (stdlib + boto3 only)."
  type        = string
}

variable "extra_files" {
  description = "Extra files to add to the zip: path inside the zip => source file (shared stdlib-only modules)."
  type        = map(string)
  default     = {}
}

variable "handler" {
  description = "Handler, e.g. handler.handler."
  type        = string
  default     = "handler.handler"
}

variable "env" {
  description = "Environment variables."
  type        = map(string)
  default     = {}
}

variable "policy_json" {
  description = "Inline IAM policy for the function's own permissions, or null."
  type        = string
  default     = null
}

variable "timeout" {
  description = "Timeout in seconds."
  type        = number
  default     = 15
}

variable "memory" {
  description = "Memory in MB."
  type        = number
  default     = 256
}

variable "log_retention_days" {
  description = "Log retention (north star 14.1: 7 days)."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
