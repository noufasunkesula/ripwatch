# worker-asg

Launch template `rw-worker-lt` (IMDSv2 required, hop limit 1, encrypted gp3 root, public IP,
instance tag `rw:role=worker`) and ASG `rw-worker-asg` (min 0, max 1, desired 0, both subnets).

- `ami_id` must be set; an empty value fails the plan with a pointer to `docs/sprint-2-inputs.md`.
- `desired_capacity` is ignored after creation so `make wake` / `make sleep` and `rw-scheduler`
  never fight Terraform.
- `GroupInServiceInstances` is collected (free) for the "worker running more than 14 h" alarm.

Outputs: `asg_name`, `launch_template_id`.
