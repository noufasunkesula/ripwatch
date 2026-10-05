variable "name" {
  description = "User pool name (rw-users)."
  type        = string
  default     = "rw-users"
}

variable "callback_urls" {
  description = "OAuth callback URLs (CloudFront domain; placeholder until edge exists)."
  type        = list(string)
}

variable "logout_urls" {
  description = "OAuth logout URLs."
  type        = list(string)
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
