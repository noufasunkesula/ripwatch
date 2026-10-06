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

variable "iam_users" {
  description = "Developer IAM users (RW_IAM_USERS). rw-noufa is made by hand and must not be listed."
  type        = list(string)
  default     = ["rw-daksh", "rw-saif"]

  validation {
    condition     = !contains(var.iam_users, "rw-noufa") && alltrue([for u in var.iam_users : startswith(u, "rw-")])
    error_message = "iam_users must start with rw- and must not include rw-noufa (the hand-made admin user)."
  }
}
