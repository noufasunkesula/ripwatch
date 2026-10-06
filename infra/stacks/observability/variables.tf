variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = "Value of the Owner tag."
  type        = string
  default     = "noufa"
}

variable "judging_mode" {
  description = "true during judging: disables the worker-running-long alarm."
  type        = bool
  default     = false
}
