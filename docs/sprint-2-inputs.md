# Sprint 2 inputs

Every value Sprint 2 still needs before `make up`, who provides it, and exactly where to get it.
Real values go in `.env` or `infra/**/terraform.tfvars` (both gitignored), never in git.
Update the Status column as values arrive. Sources: `docs/sprints/sprint-1.md` section 2.2,
`docs/02-infra-north-star.md` sections 4, 11, 13 and 20.

| Value | Where it goes | Who | How to get it | Status |
|---|---|---|---|---|
| Paid plan upgrade | Account (console) | Noufa (root, the one allowed root use) | Billing and Cost Management console, Account plan, Upgrade. Credits carry over | open |
| `AWS_ACCOUNT_ID` | `.env` | Noufa | `aws sts get-caller-identity --profile ripwatch --query Account --output text` | open |
| `aws configure --profile ripwatch` per person | `~/.aws/credentials` (never the repo) | Each person | IAM console, Users, `<user>`, Security credentials, Create access key. Then `aws sts get-caller-identity --profile ripwatch` must show the user, not root | open |
| Access keys for `rw-daksh`, `rw-saif` | Their own `~/.aws/credentials` | Daksh, Saif | After the `iam` stack apply (or made by Noufa by hand), same console page as above | open |
| `RW_TEAM_EMAILS` | `.env`, `terraform.tfvars` (bootstrap budgets, observability) | Noufa | Team members' emails, comma separated. Each must confirm the SNS subscription email | set locally for Daksh; add Noufa and Saif |
| `RW_LIFEGUARD_EMAIL` | `.env`, `terraform.tfvars` (serverless SNS) | Noufa | Plus-alias of a team inbox, e.g. `name+lifeguard@gmail.com`. Confirm the SNS subscription email | set locally |
| `RW_IAM_USERS` | `.env`, `terraform.tfvars` (iam) | Noufa | `rw-daksh,rw-saif` | set |
| COOL Marketplace subscription | Account (console) | Noufa | AWS Marketplace, search "COOL" (OpenCV optimized, Ubuntu 24.04 arm64), Subscribe. 7-day free trial; subscribe in benchmark week | open |
| COOL verification gate result | Decision Log | Noufa | Launch one `c7g` from the COOL AMI, run `python -c "import cv2; print(cv2.__version__)"` in `/opt/cool/venvs/python_3.12`. 5.x: COOL is production. 4.x: std-arm is production (north star section 0) | open |
| `RW_COOL_AMI_ID` / `cool_ami_id` | `.env`, `infra/stacks/compute/terraform.tfvars` | Noufa | After subscribing: Marketplace, Manage subscriptions, Launch, pick `us-east-1` and the version, copy the AMI ID. Or `aws ec2 describe-images --owners aws-marketplace --filters "Name=name,Values=*COOL*" --region us-east-1` | open |
| Bedrock model access for `amazon.nova-lite-v1:0` | Account (console) | Noufa | Bedrock console, `us-east-1`, Model access, enable Amazon Nova Lite. Then one smoke call from the worker role | open |
| `RW_NOAA_STATION_ID` | `.env`, `/rw/...` SSM via serverless stack | Daksh | Pick the demo beach's CO-OPS station from `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json` | open (proposed Panama City Beach FL, 8729108, unverified) |
| `RW_NWS_ZONE_ID` | `.env`, serverless stack | Daksh | `https://api.weather.gov/zones?type=forecast&point=<lat>,<lon>` for the beach | open |
| `RW_NWS_USER_AGENT` | `.env`, serverless stack | Daksh | `RipWatch/0.1 (<team email>)` | open |
| Demo beach coordinates | Camera config (`rw-cameras` seed) | Daksh | Lat/lon of the chosen beach | open |
| `HF_TOKEN` | `.env` (only for the download script) | Saif or Noufa | huggingface.co, Settings, Access Tokens (read). Accept the RipVIS dataset terms first | open |
| `RW_ALLOWED_ORIGIN` | `.env`, serverless stack | Noufa | CloudFront domain from the `edge` stack output after its first apply (second apply wires CORS and Cognito callbacks) | open |
| GitHub repo variable `RW_DEPLOY_ENABLED` | GitHub repo settings | Noufa | Settings, Secrets and variables, Actions, Variables. Set `true` only when Sprint 2 deploys start | open |
| Branch protection on `main` | GitHub repo settings | Noufa | Settings, Branches: PR required, 1 approval, CI must pass, no force pushes | open |
| Collaborators Daksh and Saif | GitHub repo settings | Noufa | Settings, Collaborators | open |
| Queue URLs, topic ARN, ASG name | `/etc/rw/rw.env` on the worker (user data) | Automatic | Looked up by name at boot (N-10); locally from `make local-up` | n/a |
