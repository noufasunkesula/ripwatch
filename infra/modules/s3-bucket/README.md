# s3-bucket

Private bucket with the RipWatch defaults: all 4 public access blocks on, `BucketOwnerEnforced`,
SSE-S3, TLS-only bucket policy (merged with `extra_policy_json`), optional versioning,
lifecycle expiry and CORS. `force_destroy` defaults to false so data is never deleted by a destroy.

```hcl
module "artifacts" {
  source     = "../../modules/s3-bucket"
  name       = "rw-artifacts-${local.account_id}"
  versioning = true
  lifecycle_rules = [
    { id = "keyframes", prefix = "keyframes/", expiration_days = 7 },
    { id = "old-versions", noncurrent_expiration_days = 14 },
  ]
}
```

Outputs: `id`, `arn`, `bucket_regional_domain_name`.
