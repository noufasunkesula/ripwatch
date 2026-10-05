# Network (sprint-1.md N-08, north star 6): one VPC, two public subnets, no NAT, no inbound.

data "aws_region" "current" {}

locals {
  subnets = {
    a = { cidr = "10.20.1.0/24", az = "${data.aws_region.current.region}a" }
    b = { cidr = "10.20.2.0/24", az = "${data.aws_region.current.region}b" }
  }
}

resource "aws_vpc" "this" {
  #checkov:skip=CKV2_AWS_11:VPC flow logs are billed per GB; the worker has no inbound path to audit
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "rw-vpc" }
}

# Locks down the VPC's default security group: no rules at all.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "rw-default-sg-unused" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "rw-igw" }
}

resource "aws_subnet" "public" {
  #checkov:skip=CKV_AWS_130:Public IPs replace a NAT gateway (north star 6); rw-worker-sg allows no inbound traffic
  for_each                = local.subnets
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value.cidr
  availability_zone       = each.value.az
  map_public_ip_on_launch = true

  tags = { Name = "rw-public-${each.key}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "rw-public-rt" }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# Gateway endpoints are free and keep S3 and DynamoDB traffic off the internet.
resource "aws_vpc_endpoint" "gateway" {
  for_each          = toset(["s3", "dynamodb"])
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.${each.value}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = { Name = "rw-${each.value}-endpoint" }
}

resource "aws_security_group" "worker" {
  #checkov:skip=CKV2_AWS_5:Attached to the worker launch template in the compute stack
  name        = "rw-worker-sg"
  description = "rw-worker: no inbound; HTTPS and time sync out"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "rw-worker-sg" }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  #checkov:skip=CKV_AWS_382:worker must reach Bedrock, NOAA, NWS, PyPI over HTTPS; no NAT by design (north star section 6)
  security_group_id = aws_security_group.worker.id
  description       = "HTTPS to AWS APIs, NOAA, NWS, PyPI"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "time_sync" {
  security_group_id = aws_security_group.worker.id
  description       = "Amazon Time Sync Service"
  ip_protocol       = "udp"
  from_port         = 123
  to_port           = 123
  cidr_ipv4         = "169.254.169.123/32"
}
