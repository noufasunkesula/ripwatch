# Rest of the backend config comes from infra/backend.hcl (make backend-config).
terraform {
  backend "s3" {
    key = "compute/terraform.tfstate"
  }
}
