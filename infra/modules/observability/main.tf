# Dashboards, alarms and saved queries (north star 14.3, 14.4, 14.1).
# Budget: 3 dashboards, under 10 alarms (north star 1).

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
  ns     = var.namespace
  tags   = merge({ ManagedBy = "terraform", Project = "ripwatch" }, var.tags)

  # One widget spec per entry; laid out two per row below.
  ops_widgets = [
    { title = "Frames per second", stat = "Average", metrics = [[local.ns, "FramesPerSecond"]] },
    { title = "Frame latency by stage (ms)", stat = "Average", search = "{${local.ns},stage,runtime} MetricName=\"FrameLatencyMs\"" },
    { title = "Queue depth", stat = "Maximum", metrics = [for q in var.queue_names : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", q]] },
    { title = "Dead-letter queues", stat = "Maximum", metrics = [for q in var.dlq_names : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", q]] },
    { title = "Jobs processed by mode", stat = "Sum", search = "{${local.ns},mode} MetricName=\"JobsProcessed\"" },
    { title = "Lambda errors", stat = "Sum", metrics = [for f in var.lambda_names : ["AWS/Lambda", "Errors", "FunctionName", f]] },
  ]

  agent_widgets = [
    { title = "Rip candidates by mode", stat = "Sum", search = "{${local.ns},mode} MetricName=\"RipCandidates\"" },
    { title = "False alarms rejected", stat = "Sum", metrics = [[local.ns, "FalseAlarmsRejected"]] },
    { title = "Incidents created", stat = "Sum", metrics = [[local.ns, "IncidentsCreated"]] },
    { title = "Agent tool calls", stat = "Sum", metrics = [[local.ns, "AgentToolCalls"]] },
    { title = "Agent decision latency (ms)", stat = "p95", metrics = [[local.ns, "AgentDecisionLatencyMs"]] },
    { title = "Fallbacks used", stat = "Sum", metrics = [[local.ns, "AgentFallbackUsed"]] },
    { title = "Bedrock tokens", stat = "Sum", metrics = [[local.ns, "BedrockTokens"]] },
    { title = "Approval latency (s)", stat = "Average", metrics = [[local.ns, "ApprovalLatencySec"]] },
  ]

  benchmark_widgets = [
    { title = "Frame latency p50 by runtime and stage (ms)", stat = "p50", search = "{${local.ns},stage,runtime} MetricName=\"FrameLatencyMs\"" },
    { title = "Frame latency p95 by runtime and stage (ms)", stat = "p95", search = "{${local.ns},stage,runtime} MetricName=\"FrameLatencyMs\"" },
    { title = "Cost per camera-hour by runtime (USD)", stat = "Average", search = "{${local.ns},runtime} MetricName=\"CostPerCameraHour\"" },
  ]

  dashboards = {
    "rw-ops"       = local.ops_widgets
    "rw-agent"     = local.agent_widgets
    "rw-benchmark" = local.benchmark_widgets
  }
}

resource "aws_cloudwatch_dashboard" "this" {
  for_each       = local.dashboards
  dashboard_name = each.key

  dashboard_body = jsonencode({
    widgets = [
      for i, w in each.value : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = floor(i / 2) * 6
        width  = 12
        height = 6
        properties = {
          title  = w.title
          region = local.region
          view   = "timeSeries"
          stat   = w.stat
          period = 60
          metrics = try(
            [[{ expression = "SEARCH('${w.search}', '${w.stat}', 60)", id = "e1", label = "" }]],
            w.metrics,
          )
        }
      }
    ]
  })
}

# ---------------------------------------------------------------- Alarms (north star 14.4)

resource "aws_cloudwatch_metric_alarm" "fps_low" {
  alarm_name          = "rw-fps-low"
  alarm_description   = "Worker below 4 frames per second for 5 minutes."
  namespace           = local.ns
  metric_name         = "FramesPerSecond"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  comparison_operator = "LessThanThreshold"
  threshold           = 4
  treat_missing_data  = "notBreaching" # The worker sleeps most of the time.
  alarm_actions       = [var.ops_topic_arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each            = toset(var.dlq_names)
  alarm_name          = "${each.value}-not-empty"
  alarm_description   = "Messages failed processing and landed in ${each.value}."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "agent_fallback_high" {
  alarm_name          = "rw-agent-fallback-high"
  alarm_description   = "More than 20% of candidates used the rule-based fallback in 15 minutes."
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 20
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  tags                = local.tags

  metric_query {
    id          = "rate"
    expression  = "IF(candidates > 0, 100 * fallbacks / candidates, 0)"
    label       = "Fallback rate (%)"
    return_data = true
  }

  metric_query {
    id = "fallbacks"
    metric {
      namespace   = local.ns
      metric_name = "AgentFallbackUsed"
      stat        = "Sum"
      period      = 900
    }
  }

  metric_query {
    id = "candidates"
    metric {
      namespace   = local.ns
      metric_name = "RipCandidates"
      stat        = "Sum"
      period      = 900
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "rw-api-errors"
  alarm_description   = "rw-api returned more than 5 errors in 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.api_lambda_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_running_long" {
  alarm_name          = "rw-worker-running-long"
  alarm_description   = "Worker has run more than 14 h straight. Forgot make sleep? (build phase only)"
  namespace           = "AWS/AutoScaling"
  metric_name         = "GroupInServiceInstances"
  dimensions          = { AutoScalingGroupName = var.asg_name }
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 14
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  actions_enabled     = var.worker_runtime_alarm_enabled
  alarm_actions       = [var.ops_topic_arn]
  tags                = local.tags
}

# ---------------------------------------------------------------- Saved Logs Insights queries

resource "aws_cloudwatch_query_definition" "errors" {
  name            = "rw-errors"
  log_group_names = var.log_group_names
  query_string    = <<-EOT
    fields @timestamp, service, msg, trace_id, incident_id, exc
    | filter level = "ERROR" or level = "CRITICAL"
    | sort @timestamp desc
    | limit 200
  EOT
}

resource "aws_cloudwatch_query_definition" "slow_frames" {
  name            = "rw-slow-frames"
  log_group_names = var.log_group_names
  query_string    = <<-EOT
    fields @timestamp, stage, runtime, FrameLatencyMs
    | filter ispresent(FrameLatencyMs) and FrameLatencyMs > 1000
    | sort FrameLatencyMs desc
    | limit 100
  EOT
}

resource "aws_cloudwatch_query_definition" "incident_timeline" {
  name            = "rw-incident-timeline"
  log_group_names = var.log_group_names
  query_string    = <<-EOT
    fields @timestamp, service, msg, incident_id, trace_id, camera_id, result_id
    | filter ispresent(incident_id) or ispresent(trace_id)
    # Narrow with: | filter incident_id = "inc_..." or trace_id = "tr_..."
    | sort @timestamp asc
    | limit 1000
  EOT
}
