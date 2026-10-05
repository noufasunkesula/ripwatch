# nosemgrep: aws-dynamodb-table-unencrypted -- encrypted with the AWS managed key (server_side_encryption below); a CMK costs monthly
resource "aws_dynamodb_table" "this" {
  #checkov:skip=CKV_AWS_28:PITR is an input, off by default to save cost; detections and traces expire anyway
  #checkov:skip=CKV_AWS_119:AWS managed encryption by design (north star 15); a CMK costs monthly
  #checkov:skip=CKV2_AWS_16:On-demand billing, no capacity to auto scale
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = var.hash_key
  range_key    = var.range_key
  tags         = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)

  dynamic "attribute" {
    for_each = var.attributes
    content {
      name = attribute.value.name
      type = attribute.value.type
    }
  }

  dynamic "global_secondary_index" {
    for_each = var.gsis
    content {
      name            = global_secondary_index.value.name
      hash_key        = global_secondary_index.value.hash_key
      range_key       = global_secondary_index.value.range_key
      projection_type = global_secondary_index.value.projection_type
    }
  }

  dynamic "ttl" {
    for_each = var.ttl_attribute == null ? [] : [var.ttl_attribute]
    content {
      attribute_name = ttl.value
      enabled        = true
    }
  }

  point_in_time_recovery {
    enabled = var.pitr
  }

  server_side_encryption {
    enabled = true
  }
}
