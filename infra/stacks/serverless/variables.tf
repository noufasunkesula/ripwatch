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

variable "lifeguard_email" {
  description = "Subscribed to rw-lifeguard-alerts (RW_LIFEGUARD_EMAIL)."
  type        = string

  validation {
    condition     = can(regex("^[^@ ]+@[^@ ]+[.][^@ ]+$", var.lifeguard_email))
    error_message = "lifeguard_email must be an email address (RW_LIFEGUARD_EMAIL)."
  }
}

variable "dashboard_url" {
  description = "Dashboard origin, e.g. https://<cloudfront-domain> (RW_ALLOWED_ORIGIN). Placeholder until the edge stack exists; second apply in Sprint 2."
  type        = string
  default     = "https://placeholder.invalid"
}

variable "noaa_station_id" {
  description = "NOAA CO-OPS station (RW_NOAA_STATION_ID, Daksh)."
  type        = string
  default     = "unset"
}

variable "nws_zone_id" {
  description = "NWS forecast zone (RW_NWS_ZONE_ID, Daksh)."
  type        = string
  default     = "unset"
}

variable "nws_user_agent" {
  description = "NWS contact User-Agent (RW_NWS_USER_AGENT), e.g. RipWatch/0.1 (team@example.com)."
  type        = string
  default     = "RipWatch/0.1"
}

variable "api_version" {
  description = "Version GET /api/health reports (RW_VERSION); deploy.yml sets TF_VAR_api_version to the git sha."
  type        = string
  default     = "dev"
}

variable "judging_mode" {
  description = "true during judging: the nightly sleep schedule is disabled so the demo stays up."
  type        = bool
  default     = false
}
