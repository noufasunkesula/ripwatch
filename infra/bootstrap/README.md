# infra/bootstrap

One-time foundation (sprint-1.md N-06, north star section 4). Uses **local state**, because it
creates the bucket every other stack keeps its state in.

| Creates | Notes |
|---|---|
| `rw-tfstate-<account_id>` | Versioned, SSE-S3, public access blocked, `prevent_destroy`, TLS only, `*.tfstate` deletes denied for everyone but the account root, old versions expire after 90 days |
| OIDC provider + role `rw-gha` | GitHub Actions on `main` and pull requests of `noufasunkesula/ripwatch`. `PowerUserAccess` plus IAM limited to `rw-*` (narrow in Sprint 3) |
| SNS `rw-ops-alerts` | Team emails. Budgets and CloudWatch may publish |
| SNS `rw-kill-switch` | No emails. Only the 80% budget notice publishes; the `rw-kill-switch` Lambda subscribes (serverless stack) |
| Budgets `rw-monthly` ($50), `rw-bedrock` ($10) | 25/50/100% to the team and `rw-ops-alerts`, 80% to `rw-kill-switch` |
| Cost anomaly monitor + daily email | $5 absolute impact |

Inputs: `terraform.tfvars` (copy `terraform.tfvars.example`), only `team_emails` is required.

## Plan and apply

```bash
make plan STACK=bootstrap                                  # READ, always allowed
make apply STACK=bootstrap RW_CONFIRM_APPLY=bootstrap      # only after a human says "apply"
```

After the apply, every team email gets two confirmation emails from AWS (SNS). Click them,
or alerts never arrive.

## Migrate this state into the bucket (Sprint 2, after the first apply)

Local state on one laptop is a single point of failure. Once the bucket exists:

1. `make backend-config` (writes `infra/backend.hcl`).
2. Add `infra/bootstrap/backend.tf`:
   ```hcl
   terraform {
     backend "s3" {
       key = "bootstrap/terraform.tfstate"
     }
   }
   ```
3. `cd infra/bootstrap && terraform init -migrate-state -backend-config=../backend.hcl`, answer
   `yes` to copy the state.
4. Check `terraform plan` shows no changes, then delete the local `terraform.tfstate*` files.
5. Update `scripts/tf.sh` so `init bootstrap` uses the S3 backend like the other stacks.

## Notes

- Only one `DIMENSIONAL` / `SERVICE` anomaly monitor is allowed per account. If the account
  already has one (Cost Explorer sometimes creates a default), import it instead of creating:
  `terraform import aws_ce_anomaly_monitor.services <arn>`.
- Destroying needs `RW_CONFIRM_APPLY=destroy-bootstrap-really` and still fails on the state
  bucket because of `prevent_destroy`. That is intended.
