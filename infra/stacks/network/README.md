# stacks/network

North star 6: `rw-vpc` (10.20.0.0/16), public subnets `rw-public-a` / `rw-public-b` in two AZs,
internet gateway, one route table, free gateway endpoints for S3 and DynamoDB, and
`rw-worker-sg` with **no inbound rules** (egress HTTPS anywhere and UDP 123 to Amazon Time Sync).
The default security group is emptied. No NAT gateway, no interface endpoints (hourly cost).

Plan before the state bucket exists: `make plan-local STACK=network`.

Outputs: `vpc_id`, `subnet_ids`, `worker_sg_id`, `route_table_id`.
