variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = "Value of the Owner tag."
  type        = string
  default     = "noufa"
}

variable "github_owner" {
  description = "GitHub user or org that owns the repo (OIDC trust for rw-gha)."
  type        = string
  default     = "noufasunkesula"
}

variable "github_repo" {
  description = "GitHub repository name (OIDC trust for rw-gha)."
  type        = string
  default     = "ripwatch"
}

variable "team_emails" {
  description = "Team emails for budget notices, anomaly reports and rw-ops-alerts (RW_TEAM_EMAILS)."
  type        = list(string)

  validation {
    condition     = length(var.team_emails) > 0 && alltrue([for e in var.team_emails : can(regex("^[^@ ]+@[^@ ]+[.][^@ ]+$", e))])
    error_message = "team_emails must hold at least one valid email address (RW_TEAM_EMAILS)."
  }
}

variable "budget_usd" {
  description = "Monthly cost budget rw-monthly in USD (RW_BUDGET_USD)."
  type        = number
  default     = 50
}

variable "bedrock_budget_usd" {
  description = "Monthly Bedrock budget rw-bedrock in USD (RW_BEDROCK_BUDGET_USD)."
  type        = number
  default     = 10
}

variable "anomaly_threshold_usd" {
  description = "Cost anomaly email threshold, absolute impact in USD."
  type        = number
  default     = 5
}
