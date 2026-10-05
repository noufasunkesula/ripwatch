# dynamodb-table

On-demand (`PAY_PER_REQUEST`) table with encryption on, optional TTL, GSIs and PITR (off by
default). List only key attributes (table and GSI keys) in `attributes`.

```hcl
module "incidents" {
  source     = "../../modules/dynamodb-table"
  name       = "rw-incidents"
  hash_key   = "incident_id"
  attributes = [
    { name = "incident_id", type = "S" },
    { name = "status", type = "S" },
    { name = "created_at", type = "S" },
  ]
  gsis = [{ name = "status-index", hash_key = "status", range_key = "created_at" }]
}
```

Outputs: `name`, `arn`, `stream_arn` (null).
