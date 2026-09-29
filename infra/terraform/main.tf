data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  https   = var.certificate_arn != ""
  app_url = local.https && var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.this.dns_name}"

  # Cloud Map private DNS: services reach each other as <service>.<namespace>.
  namespace   = "${var.project}.internal"
  qdrant_url  = "http://qdrant.${local.namespace}:6333"
  backend_url = "http://backend.${local.namespace}:8000"

  ssm_prefix = "/${var.project}/${var.environment}"
  ecr_url    = "${local.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"

  capacity_provider = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"
}
