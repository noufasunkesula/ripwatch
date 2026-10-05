variable "name" {
  description = "Table name, e.g. rw-incidents."
  type        = string
}

variable "hash_key" {
  description = "Partition key attribute name."
  type        = string
}

variable "range_key" {
  description = "Sort key attribute name, or null."
  type        = string
  default     = null
}

variable "attributes" {
  description = "Key attributes (table and GSI keys only). type is S, N or B."
  type = list(object({
    name = string
    type = string
  }))
}

variable "ttl_attribute" {
  description = "Epoch-seconds attribute that expires items, or null."
  type        = string
  default     = null
}

variable "gsis" {
  description = "Global secondary indexes."
  type = list(object({
    name            = string
    hash_key        = string
    range_key       = optional(string)
    projection_type = optional(string, "ALL")
  }))
  default = []
}

variable "pitr" {
  description = "Point-in-time recovery (costs per GB-month)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
