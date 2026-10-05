terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Local state: this stack creates the state bucket. Migrated into it after the first apply
  # (see README.md).
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "ripwatch"
      Owner     = var.owner
      Component = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}
