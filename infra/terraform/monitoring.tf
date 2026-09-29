# Alarms on the app's EMF metrics (backend/app/observability/metrics.py), the ALB and the logs.
# Every alarm notifies one SNS topic; confirm the subscription email after the first apply.

locals {
  metrics_namespace = "SecureRagAssistant"
  env_dimension     = { Environment = var.environment }
}

# Not KMS-encrypted on purpose: CloudWatch cannot publish to a topic that uses the AWS-managed
# SNS key, and alarm notifications carry no sensitive data.
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------- LLM cost and usage ----------
resource "aws_cloudwatch_metric_alarm" "daily_llm_cost" {
  alarm_name          = "${local.name}-daily-llm-cost"
  alarm_description   = "Metered LLM cost over the last day exceeded $${var.daily_llm_cost_alarm_usd}."
  namespace           = local.metrics_namespace
  metric_name         = "CostUSD"
  dimensions          = local.env_dimension
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.daily_llm_cost_alarm_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "token_spike" {
  alarm_name          = "${local.name}-token-spike"
  alarm_description   = "More than ${var.token_spike_per_5min} tokens in 5 minutes: runaway loop or abuse."
  namespace           = local.metrics_namespace
  metric_name         = "TotalTokens"
  dimensions          = local.env_dimension
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.token_spike_per_5min
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------- Reliability ----------
resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${local.name}-error-rate"
  alarm_description   = "More than ${var.error_rate_alarm_percent}% of chat requests failed."
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.error_rate_alarm_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "rate"
    expression  = "IF(requests > 0, 100 * errors / requests, 0)"
    label       = "Error rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      namespace   = local.metrics_namespace
      metric_name = "Errors"
      dimensions  = local.env_dimension
      stat        = "Sum"
      period      = 300
    }
  }

  metric_query {
    id = "requests"
    metric {
      namespace   = local.metrics_namespace
      metric_name = "Requests"
      dimensions  = local.env_dimension
      stat        = "Sum"
      period      = 300
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "p95_latency" {
  alarm_name          = "${local.name}-p95-latency"
  alarm_description   = "p95 chat latency above ${var.p95_latency_alarm_ms} ms for 15 minutes."
  namespace           = local.metrics_namespace
  metric_name         = "LatencyMs"
  dimensions          = local.env_dimension
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.p95_latency_alarm_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-alb-5xx"
  alarm_description   = "The load balancer returned more than 10 5xx responses in 5 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_ELB_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  for_each = {
    backend  = aws_lb_target_group.backend.arn_suffix
    frontend = aws_lb_target_group.frontend.arn_suffix
  }

  alarm_name          = "${local.name}-${each.key}-unhealthy"
  alarm_description   = "No healthy ${each.key} targets behind the load balancer."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix, TargetGroup = each.value }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# ---------- Security ----------
resource "aws_cloudwatch_metric_alarm" "access_denied_spike" {
  alarm_name          = "${local.name}-access-denied-spike"
  alarm_description   = "Cross-department requests spiked: someone may be probing for restricted data."
  namespace           = local.metrics_namespace
  metric_name         = "AccessDenied"
  dimensions          = local.env_dimension
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.access_denied_per_5min
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The retriever logs this if the vector store ever returns a chunk the role may not read
# (defence in depth behind the Qdrant filter). It should never happen, so alarm on the first one.
resource "aws_cloudwatch_log_metric_filter" "rbac_invariant" {
  name           = "${local.name}-rbac-invariant-violation"
  log_group_name = aws_cloudwatch_log_group.this["backend"].name
  pattern        = "\"RBAC invariant violated\""

  metric_transformation {
    namespace = local.metrics_namespace
    name      = "RbacInvariantViolations"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "rbac_invariant" {
  alarm_name          = "${local.name}-rbac-invariant-violation"
  alarm_description   = "A restricted chunk reached the retriever and was dropped. Check the Qdrant filter."
  namespace           = local.metrics_namespace
  metric_name         = "RbacInvariantViolations"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------- Infrastructure spend ----------
# Account-wide: tag-filtered budgets only work once the Project tag is activated for cost
# allocation in the Billing console.
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
