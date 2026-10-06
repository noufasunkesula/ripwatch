# lambda-fn

Python 3.12 arm64 function zipped from `source_dir` (stdlib + boto3 only), its own role with
basic execution plus `policy_json`, and a log group with 7-day retention created **before**
the function.

```hcl
module "ocean_poller" {
  source      = "../../modules/lambda-fn"
  name        = "rw-ocean-poller"
  source_dir  = "${path.root}/../../../lambdas/ocean_poller"
  timeout     = 30
  env         = { RW_NOAA_STATION_ID = var.noaa_station_id }
  policy_json = data.aws_iam_policy_document.ocean_poller.json
}
```

`extra_files` adds shared stdlib-only files (for `rw-api`: `rw/contracts/enums.py`, `rw/agent/lifecycle_rules.py`). The zip is written to `<stack>/.build/` (gitignored). Outputs: `name`, `arn`, `invoke_arn`,
`role_arn`, `role_name`.
