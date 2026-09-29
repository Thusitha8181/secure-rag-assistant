resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # the app's own EMF metrics cover what the dashboard needs, at no extra cost
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each = toset(["backend", "frontend", "qdrant", "ingest"])

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

# ---------- Service discovery (Cloud Map) ----------
resource "aws_service_discovery_private_dns_namespace" "this" {
  name = local.namespace
  vpc  = aws_vpc.this.id
}

resource "aws_service_discovery_service" "this" {
  for_each = toset(["qdrant", "backend"])

  name = each.key

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }
}

locals {
  log_config = {
    for svc, group in aws_cloudwatch_log_group.this : svc => {
      logDriver = "awslogs"
      options = {
        awslogs-group         = group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = svc
        mode                  = "non-blocking" # never stall a request on log back-pressure
        max-buffer-size       = "4m"
      }
    }
  }

  image = { for k, repo in aws_ecr_repository.this : k => "${repo.repository_url}:${var.image_tag}" }

  backend_environment = [
    for k, v in {
      ENVIRONMENT       = var.environment
      APP_VERSION       = var.image_tag
      QDRANT_URL        = local.qdrant_url
      USAGE_BACKEND     = "dynamodb"
      DYNAMODB_TABLE    = aws_dynamodb_table.usage.name
      AWS_REGION        = var.aws_region
      EMF_ENABLED       = "true"
      DAILY_TOKEN_QUOTA = tostring(var.daily_token_quota)
      DEMO_MODE         = "true"
      CORS_ORIGINS      = jsonencode([local.app_url])
      LANGSMITH_TRACING = tostring(var.langsmith_tracing)
      LANGSMITH_PROJECT = var.langsmith_project
    } : { name = k, value = v }
  ]
}

# ---------- Task definitions ----------
resource "aws_ecs_task_definition" "qdrant" {
  family                   = "${local.name}-qdrant"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.qdrant.cpu
  memory                   = var.qdrant.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.qdrant_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "storage"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.qdrant.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.qdrant.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name         = "qdrant"
    image        = var.qdrant.image
    essential    = true
    portMappings = [{ containerPort = 6333, protocol = "tcp" }]
    environment = [
      { name = "QDRANT__TELEMETRY_DISABLED", value = "true" },
    ]
    secrets = [
      { name = "QDRANT__SERVICE__API_KEY", valueFrom = local.secret_arns.QDRANT_API_KEY },
    ]
    mountPoints = [{ sourceVolume = "storage", containerPath = "/qdrant/storage" }]
    healthCheck = {
      command     = ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    logConfiguration = local.log_config.qdrant
  }])
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend.cpu
  memory                   = var.backend.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.backend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "backend"
    image        = local.image.backend
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = local.backend_environment
    secrets      = [for k, arn in local.secret_arns : { name = k, valueFrom = arn }]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 90
    }
    logConfiguration = local.log_config.backend
  }])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.frontend.cpu
  memory                   = var.frontend.memory
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "frontend"
    image        = local.image.frontend
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment = [
      # The ALB sends /api/* straight to the backend; this only serves the Next.js proxy route.
      { name = "BACKEND_URL", value = local.backend_url },
    ]
    logConfiguration = local.log_config.frontend
  }])
}

# Run on demand (CI or `make ingest-aws`); rebuilds the collection from the data baked into the image.
resource "aws_ecs_task_definition" "ingest" {
  family                   = "${local.name}-ingest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ingest.cpu
  memory                   = var.ingest.memory
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "ingest"
    image     = local.image.ingest
    essential = true
    environment = [
      { name = "QDRANT_URL", value = local.qdrant_url },
    ]
    secrets = [
      { name = "QDRANT_API_KEY", valueFrom = local.secret_arns.QDRANT_API_KEY },
    ]
    logConfiguration = local.log_config.ingest
  }])
}

# ---------- Services ----------
resource "aws_ecs_service" "qdrant" {
  name            = "qdrant"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.qdrant.arn
  desired_count   = 1

  # Stop the old task before starting the new one: never two writers on the same EFS data.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.qdrant.id]
    assign_public_ip = true # no NAT: a public IP is the only way to pull the image
  }

  service_registries {
    registry_arn = aws_service_discovery_service.this["qdrant"].arn
  }

  depends_on = [aws_efs_mount_target.qdrant]
}

resource "aws_ecs_service" "backend" {
  name                              = "backend"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.backend.arn
  desired_count                     = var.backend.desired_count
  health_check_grace_period_seconds = 120 # models, spaCy and the graph load before /api/health is up

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  service_registries {
    registry_arn = aws_service_discovery_service.this["backend"].arn
  }

  # deploy.yml registers new revisions with git-SHA images; Terraform must not revert them.
  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener_rule.api]
}

resource "aws_ecs_service" "frontend" {
  name                              = "frontend"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.frontend.arn
  desired_count                     = var.frontend.desired_count
  health_check_grace_period_seconds = 30

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 3000
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.http, aws_lb_listener.https]
}
