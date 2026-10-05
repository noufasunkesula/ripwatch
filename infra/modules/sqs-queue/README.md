# sqs-queue

Standard queue plus `<name>-dlq` (14 day retention), redrive after `max_receive_count` (3)
receives, SSE-SQS on both, long polling (20 s).

```hcl
module "jobs" {
  source               = "../../modules/sqs-queue"
  name                 = "rw-jobs"
  visibility_timeout_s = 300
}
```

Outputs: `name`, `url`, `arn`, `dlq_url`, `dlq_arn`, `dlq_name`. Queue policies (for example
S3 to `rw-jobs`) are added by the stack that needs them.
