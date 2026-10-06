# stacks/iam

Developer users (`rw-daksh`, `rw-saif`) in group `rw-developers` (sprint-1.md N-08, north star 5).
`rw-noufa` is created by hand with `AdministratorAccess` and is not managed here.

- No `aws_iam_user_login_profile` and no `aws_iam_access_key`: passwords and keys never land in
  Terraform state. After apply, Noufa sets a console password by hand (must change on first
  login); each person enables MFA, then creates their own access key and runs
  `aws configure --profile ripwatch`.
- `rw-require-mfa-console`: console sessions without MFA can only set up MFA and change their
  password. CLI calls with access keys are not affected.
- `rw-developers`: read `rw-data-*`, read/write `rw-artifacts-*`, read logs, metrics and
  dashboards, invoke Bedrock, SSM sessions only to instances tagged `rw:role=worker`, read
  `/rw/*` parameters and `rw-*` tables. Deletes on `rw-data-*/raw/*` and `rw-tfstate-*` are
  explicitly denied.

Needs the bootstrap apply first (state bucket).
