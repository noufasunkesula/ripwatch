variable "name" {
  description = "Distribution comment / name (rw-cdn)."
  type        = string
  default     = "rw-cdn"
}

variable "bucket_regional_domain_name" {
  description = "Regional domain name of the rw-frontend bucket."
  type        = string
}

variable "api_origin_domain" {
  description = "HTTP API domain without scheme, e.g. abc123.execute-api.us-east-1.amazonaws.com."
  type        = string
}

variable "price_class" {
  description = "CloudFront price class."
  type        = string
  default     = "PriceClass_100"
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
