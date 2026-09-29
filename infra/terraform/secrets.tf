# SSM SecureString parameters, injected into containers by ECS at start-up. Values are written
# through write-only attributes, so they never appear in the Terraform state or plan output.
# To rotate: export new TF_VAR_* values, bump var.secrets_version, apply, then redeploy.

ephemeral "random_password" "jwt_secret" {
  length  = 48
  special = false
}

ephemeral "random_password" "qdrant_api_key" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "groq_api_key" {
  name             = "${local.ssm_prefix}/GROQ_API_KEY"
  description      = "Groq API key for the assistant"
  type             = "SecureString"
  value_wo         = coalesce(var.groq_api_key, "unset")
  value_wo_version = var.secrets_version
}

resource "aws_ssm_parameter" "langsmith_api_key" {
  name             = "${local.ssm_prefix}/LANGSMITH_API_KEY"
  description      = "LangSmith API key for tracing"
  type             = "SecureString"
  value_wo         = coalesce(var.langsmith_api_key, "unset")
  value_wo_version = var.secrets_version
}

resource "aws_ssm_parameter" "jwt_secret" {
  name             = "${local.ssm_prefix}/JWT_SECRET"
  description      = "Signing key for session JWTs"
  type             = "SecureString"
  value_wo         = ephemeral.random_password.jwt_secret.result
  value_wo_version = var.secrets_version
}

resource "aws_ssm_parameter" "qdrant_api_key" {
  name             = "${local.ssm_prefix}/QDRANT_API_KEY"
  description      = "API key shared by Qdrant and its clients"
  type             = "SecureString"
  value_wo         = ephemeral.random_password.qdrant_api_key.result
  value_wo_version = var.secrets_version
}

locals {
  secret_arns = {
    GROQ_API_KEY      = aws_ssm_parameter.groq_api_key.arn
    LANGSMITH_API_KEY = aws_ssm_parameter.langsmith_api_key.arn
    JWT_SECRET        = aws_ssm_parameter.jwt_secret.arn
    QDRANT_API_KEY    = aws_ssm_parameter.qdrant_api_key.arn
  }
}
