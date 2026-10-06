variable "name" {
  description = "Bucket name, e.g. rw-data-<account_id>."
  type        = string
}

variable "versioning" {
  description = "Enable object versioning."
  type        = bool
  default     = false
}

variable "force_destroy" {
  description = "Allow destroying a non-empty bucket. Keep false for anything holding data."
  type        = bool
  default     = false
}

variable "lifecycle_rules" {
  description = "Expiry rules. prefix \"\" means the whole bucket."
  type = list(object({
    id                         = string
    prefix                     = optional(string, "")
    expiration_days            = optional(number)
    noncurrent_expiration_days = optional(number)
  }))
  default = []
}

variable "cors_rules" {
  description = "CORS rules, e.g. browser uploads from the CloudFront domain."
  type = list(object({
    allowed_methods = list(string)
    allowed_origins = list(string)
    allowed_headers = optional(list(string), ["*"])
    expose_headers  = optional(list(string), ["ETag"])
    max_age_seconds = optional(number, 3000)
  }))
  default = []
}

variable "extra_policy_json" {
  description = "Extra bucket policy statements (JSON) merged with the TLS-only policy."
  type        = string
  default     = null
}

variable "manage_policy" {
  description = "Create the bucket policy here. false when another stack owns the whole policy (it must keep the TLS-only deny)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
