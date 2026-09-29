locals {
  ns  = local.metrics_namespace
  env = var.environment

  # SEARCH expressions pick up every Role / Model value without listing them here.
  search = {
    tokens_by_role = "SEARCH('{${local.ns},Environment,Role} MetricName=\"TotalTokens\" Environment=\"${local.env}\"', 'Sum', 3600)"
    cost_by_model  = "SEARCH('{${local.ns},Environment,Model} MetricName=\"ModelCostUSD\" Environment=\"${local.env}\"', 'Sum', 3600)"
  }

  alarm_arns = concat(
    [
      aws_cloudwatch_metric_alarm.daily_llm_cost.arn,
      aws_cloudwatch_metric_alarm.token_spike.arn,
      aws_cloudwatch_metric_alarm.error_rate.arn,
      aws_cloudwatch_metric_alarm.p95_latency.arn,
      aws_cloudwatch_metric_alarm.alb_5xx.arn,
      aws_cloudwatch_metric_alarm.access_denied_spike.arn,
      aws_cloudwatch_metric_alarm.rbac_invariant.arn,
    ],
    [for a in aws_cloudwatch_metric_alarm.unhealthy_targets : a.arn],
  )

  widget_defaults = { region = var.aws_region, view = "timeSeries", stacked = false }

  widgets = [
    {
      type       = "alarm", x = 0, y = 0, width = 24, height = 3
      properties = { title = "Alarms", alarms = local.alarm_arns }
    },
    {
      type = "metric", x = 0, y = 3, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title = "Chat requests and outcomes (per 5 min)"
        stat  = "Sum", period = 300
        metrics = [
          [local.ns, "Requests", "Environment", local.env, { label = "Requests" }],
          [local.ns, "Errors", "Environment", local.env, { label = "Errors", color = "#d62728" }],
          [local.ns, "GuardrailBlocked", "Environment", local.env, { label = "Blocked (injection / out of scope)" }],
          [local.ns, "AccessDenied", "Environment", local.env, { label = "Access denied (RBAC)" }],
          [local.ns, "PIIRedacted", "Environment", local.env, { label = "PII redacted" }],
        ]
      })
    },
    {
      type = "metric", x = 12, y = 3, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title  = "Chat latency (ms)"
        period = 300
        metrics = [
          [local.ns, "LatencyMs", "Environment", local.env, { stat = "p50", label = "p50" }],
          [local.ns, "LatencyMs", "Environment", local.env, { stat = "p95", label = "p95" }],
          [local.ns, "LatencyMs", "Environment", local.env, { stat = "p99", label = "p99" }],
        ]
        annotations = { horizontal = [{ label = "p95 alarm", value = var.p95_latency_alarm_ms }] }
      })
    },
    {
      type = "metric", x = 0, y = 9, width = 8, height = 6
      properties = merge(local.widget_defaults, {
        title = "LLM cost (USD per hour)"
        stat  = "Sum", period = 3600
        metrics = [
          [local.ns, "CostUSD", "Environment", local.env, { label = "Cost", color = "#2ca02c" }],
        ]
      })
    },
    {
      type = "metric", x = 8, y = 9, width = 8, height = 6
      properties = merge(local.widget_defaults, {
        title = "LLM cost by model (USD per hour)"
        metrics = [
          [{ expression = local.search.cost_by_model, id = "cost", label = "" }],
        ]
      })
    },
    {
      type = "metric", x = 16, y = 9, width = 8, height = 6
      properties = merge(local.widget_defaults, {
        title   = "Tokens (per 5 min)"
        stat    = "Sum", period = 300
        stacked = true
        metrics = [
          [local.ns, "InputTokens", "Environment", local.env, { label = "Input" }],
          [local.ns, "OutputTokens", "Environment", local.env, { label = "Output" }],
        ]
      })
    },
    {
      type = "metric", x = 0, y = 15, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title   = "Tokens by role (per hour)"
        stacked = true
        metrics = [
          [{ expression = local.search.tokens_by_role, id = "tokens", label = "" }],
        ]
      })
    },
    {
      type = "metric", x = 12, y = 15, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title = "Security signals"
        stat  = "Sum", period = 300
        metrics = [
          [local.ns, "AccessDenied", "Environment", local.env, { label = "Access denied" }],
          [local.ns, "GuardrailBlocked", "Environment", local.env, { label = "Guardrail blocked" }],
          [local.ns, "RbacInvariantViolations", { label = "RBAC invariant violations", color = "#d62728" }],
        ]
      })
    },
    {
      type = "metric", x = 0, y = 21, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title  = "Load balancer"
        period = 300
        metrics = [
          ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "Sum", label = "Requests" }],
          ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "Sum", label = "Target 5xx" }],
          ["AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "Sum", label = "ALB 5xx" }],
          ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "p95", label = "Response time p95 (s)", yAxis = "right" }],
        ]
      })
    },
    {
      type = "metric", x = 12, y = 21, width = 12, height = 6
      properties = merge(local.widget_defaults, {
        title = "ECS utilization (%)"
        stat  = "Average", period = 300
        metrics = flatten([
          for svc in ["backend", "frontend", "qdrant"] : [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.this.name, "ServiceName", svc, { label = "${svc} CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.this.name, "ServiceName", svc, { label = "${svc} memory" }],
          ]
        ])
      })
    },
  ]
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = local.name
  dashboard_body = jsonencode({ widgets = local.widgets })
}
