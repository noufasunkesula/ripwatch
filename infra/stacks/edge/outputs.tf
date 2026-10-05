output "cloudfront_domain" {
  description = "Dashboard host. Use it for RW_ALLOWED_ORIGIN, data allowed_origin and serverless dashboard_url (second apply)."
  value       = module.site.domain_name
}

output "distribution_id" {
  description = "For cache invalidation in deploy.yml."
  value       = module.site.distribution_id
}
