variable "ami_id" {
  description = "Worker AMI (COOL AMI from the Marketplace subscription, RW_COOL_AMI_ID)."
  type        = string

  validation {
    condition     = length(trimspace(var.ami_id)) > 0
    error_message = "ami_id is empty: subscribe to COOL on AWS Marketplace and set cool_ami_id (RW_COOL_AMI_ID). See docs/sprint-2-inputs.md."
  }
}

variable "instance_type" {
  description = "Worker instance type."
  type        = string
  default     = "c7g.large"
}

variable "subnet_ids" {
  description = "Public subnets (both AZs)."
  type        = list(string)
}

variable "security_group_id" {
  description = "rw-worker-sg."
  type        = string
}

variable "instance_profile_name" {
  description = "Instance profile of rw-worker-role."
  type        = string
}

variable "user_data" {
  description = "Rendered user data script (plain text)."
  type        = string
}

variable "volume_gb" {
  description = "Root volume size in GB (gp3)."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags merged with the module defaults."
  type        = map(string)
  default     = {}
}
