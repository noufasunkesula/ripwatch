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

variable "cool_ami_id" {
  description = "COOL AMI from the Marketplace subscription (RW_COOL_AMI_ID). Empty until Sprint 2; plan fails with a clear message until set."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "Worker instance type (north star 8.1)."
  type        = string
  default     = "c7g.large"
}

variable "runtime" {
  description = "Vision runtime on the worker: cool, std-arm or std-x86 (north star 0)."
  type        = string
  default     = "cool"

  validation {
    condition     = contains(["cool", "std-arm", "std-x86"], var.runtime)
    error_message = "runtime must be cool, std-arm or std-x86."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model the worker may invoke (RW_BEDROCK_MODEL_ID)."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}
