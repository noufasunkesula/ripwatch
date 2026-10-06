output "vpc_id" {
  description = "rw-vpc."
  value       = aws_vpc.this.id
}

output "subnet_ids" {
  description = "rw-public-a and rw-public-b."
  value       = [for k in sort(keys(aws_subnet.public)) : aws_subnet.public[k].id]
}

output "worker_sg_id" {
  description = "rw-worker-sg."
  value       = aws_security_group.worker.id
}

output "route_table_id" {
  description = "Public route table (gateway endpoints attached)."
  value       = aws_route_table.public.id
}
