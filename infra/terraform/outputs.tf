output "app_url" {
  description = "Public URL of the assistant."
  value       = local.app_url
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.this.dashboard_name}"
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "ssm_parameters" {
  description = "Secrets injected into the containers (values are write-only)."
  value       = { for k, p in local.secret_arns : k => "${local.ssm_prefix}/${k}" }
}

# `make gh-vars` copies these into GitHub Actions repository variables for deploy.yml.
output "github_variables" {
  value = {
    AWS_REGION            = var.aws_region
    AWS_DEPLOY_ROLE_ARN   = aws_iam_role.github_deploy.arn
    APP_URL               = local.app_url
    ECS_CLUSTER           = aws_ecs_cluster.this.name
    ECR_REGISTRY          = local.ecr_url
    ECR_REPOSITORY_PREFIX = var.project
    BACKEND_TASK_FAMILY   = aws_ecs_task_definition.backend.family
    FRONTEND_TASK_FAMILY  = aws_ecs_task_definition.frontend.family
    INGEST_TASK_FAMILY    = aws_ecs_task_definition.ingest.family
    INGEST_LOG_GROUP      = aws_cloudwatch_log_group.this["ingest"].name
    INGEST_SUBNETS        = join(",", aws_subnet.public[*].id)
    INGEST_SECURITY_GROUP = aws_security_group.ingest.id
  }
}
