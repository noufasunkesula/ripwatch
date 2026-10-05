locals {
  tags          = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)
  instance_tags = merge(local.tags, { Name = "rw-worker", "rw:role" = "worker" })
}

resource "aws_launch_template" "this" {
  #checkov:skip=CKV_AWS_88:Public IP replaces a NAT gateway (north star 6); rw-worker-sg has no ingress
  name                   = "rw-worker-lt"
  image_id               = var.ami_id
  instance_type          = var.instance_type
  update_default_version = true
  user_data              = base64encode(var.user_data)
  tags                   = local.tags

  iam_instance_profile {
    name = var.instance_profile_name
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/sda1"

    ebs {
      volume_type           = "gp3"
      volume_size           = var.volume_gb
      encrypted             = true
      delete_on_termination = true
    }
  }

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [var.security_group_id]
    delete_on_termination       = true
  }

  tag_specifications {
    resource_type = "instance"
    tags          = local.instance_tags
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { Name = "rw-worker" })
  }
}

resource "aws_autoscaling_group" "this" {
  name                = "rw-worker-asg"
  min_size            = 0
  max_size            = 1
  desired_capacity    = 0
  vpc_zone_identifier = var.subnet_ids
  health_check_type   = "EC2"
  enabled_metrics     = ["GroupInServiceInstances"]

  launch_template {
    id      = aws_launch_template.this.id
    version = "$Latest"
  }

  # Instances are tagged by the launch template; these tags are for the ASG itself.
  dynamic "tag" {
    for_each = local.instance_tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = false
    }
  }

  # rw-scheduler and make wake/sleep change desired capacity; Terraform must not undo that.
  lifecycle {
    ignore_changes = [desired_capacity]
  }
}
