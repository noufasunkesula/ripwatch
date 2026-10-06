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

variable "allowed_origin" {
  description = "Dashboard origin allowed to upload to rw-data (CloudFront domain from the edge stack, RW_ALLOWED_ORIGIN). Placeholder until the second apply."
  type        = string
  default     = "https://placeholder.invalid"
}

variable "bedrock_model_id" {
  description = "Initial /rw/bedrock/model_id (RW_BEDROCK_MODEL_ID)."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "noaa_station_id" {
  description = "Initial /rw/noaa/station_id (RW_NOAA_STATION_ID, Daksh). \"unset\" until chosen."
  type        = string
  default     = "unset"
}

variable "nws_zone_id" {
  description = "Initial /rw/nws/zone_id (RW_NWS_ZONE_ID, Daksh). \"unset\" until chosen."
  type        = string
  default     = "unset"
}
