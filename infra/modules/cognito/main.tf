data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  tags = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)
  # Stable, account-unique suffix without adding the random provider (Decision Log 2026-10-05).
  domain = "rw-${substr(sha1("${data.aws_caller_identity.current.account_id}-${var.name}"), 0, 8)}"
}

resource "aws_cognito_user_pool" "this" {
  name                     = var.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"
  deletion_protection      = "INACTIVE"
  tags                     = local.tags

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "spa" {
  name                                 = "${var.name}-dashboard"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
  prevent_user_existence_errors        = "ENABLED"
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 7

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.domain
  user_pool_id = aws_cognito_user_pool.this.id
}
