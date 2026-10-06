output "data_bucket" {
  description = "rw-data-<account_id>."
  value       = module.data_bucket.id
}

output "data_bucket_arn" {
  description = "rw-data ARN."
  value       = module.data_bucket.arn
}

output "artifacts_bucket" {
  description = "rw-artifacts-<account_id>."
  value       = module.artifacts_bucket.id
}

output "artifacts_bucket_arn" {
  description = "rw-artifacts ARN."
  value       = module.artifacts_bucket.arn
}

output "frontend_bucket" {
  description = "rw-frontend-<account_id>."
  value       = module.frontend_bucket.id
}

output "frontend_bucket_arn" {
  description = "rw-frontend ARN."
  value       = module.frontend_bucket.arn
}

output "frontend_bucket_regional_domain_name" {
  description = "CloudFront origin domain for the dashboard."
  value       = module.frontend_bucket.bucket_regional_domain_name
}

output "table_names" {
  description = "Table key (cameras, jobs, detections, incidents, trace, approvals) to name."
  value = {
    cameras    = module.table_cameras.name
    jobs       = module.table_jobs.name
    detections = module.table_detections.name
    incidents  = module.table_incidents.name
    trace      = module.table_trace.name
    approvals  = module.table_approvals.name
  }
}

output "table_arns" {
  description = "Table key to ARN (indexes are <arn>/index/*)."
  value = {
    cameras    = module.table_cameras.arn
    jobs       = module.table_jobs.arn
    detections = module.table_detections.arn
    incidents  = module.table_incidents.arn
    trace      = module.table_trace.arn
    approvals  = module.table_approvals.arn
  }
}

output "queue_jobs_url" {
  description = "RW_QUEUE_JOBS_URL."
  value       = module.queue_jobs.url
}

output "queue_jobs_arn" {
  description = "rw-jobs ARN."
  value       = module.queue_jobs.arn
}

output "queue_candidates_url" {
  description = "RW_QUEUE_CANDIDATES_URL."
  value       = module.queue_candidates.url
}

output "queue_candidates_arn" {
  description = "rw-candidates ARN."
  value       = module.queue_candidates.arn
}

output "dlq_names" {
  description = "Dead-letter queue names (alarms)."
  value       = [module.queue_jobs.dlq_name, module.queue_candidates.dlq_name]
}
