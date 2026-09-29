# ---------- General ----------
variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "secure-rag-assistant"
}

variable "environment" {
  description = "Deployment environment. Also the Environment dimension on the app's metrics."
  type        = string
  default     = "prod"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs allowed to reach the load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ---------- TLS (optional) ----------
variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Empty serves plain HTTP on the ALB DNS name (demo only)."
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Hostname covered by certificate_arn, e.g. chat.example.com."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Hosted zone for domain_name. When set, an alias record to the ALB is created."
  type        = string
  default     = ""
}

# ---------- Compute ----------
variable "image_tag" {
  description = "Image tag used by Terraform-registered task definitions. CI deploys pin a git SHA."
  type        = string
  default     = "latest"
}

variable "use_fargate_spot" {
  description = "Run services on Fargate Spot (about 70% cheaper, tasks can be interrupted)."
  type        = bool
  default     = true
}

variable "backend" {
  description = "Backend task size. The reranker and embedding models run on CPU in-process."
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
  })
  default = { cpu = 1024, memory = 2048, desired_count = 1 }
}

variable "frontend" {
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
  })
  default = { cpu = 256, memory = 512, desired_count = 1 }
}

variable "qdrant" {
  description = "Qdrant task size. Always a single task: two writers on one EFS volume corrupt it."
  type = object({
    cpu    = number
    memory = number
    image  = string
  })
  default = { cpu = 512, memory = 1024, image = "qdrant/qdrant:v1.19.1" }
}

variable "ingest" {
  description = "One-off ingestion task (Docling). Needs more memory than the API."
  type = object({
    cpu    = number
    memory = number
  })
  default = { cpu = 1024, memory = 4096 }
}

variable "log_retention_days" {
  type    = number
  default = 30
}

# ---------- App settings ----------
variable "daily_token_quota" {
  description = "Per-user daily token quota enforced by the API (HTTP 429 once exceeded)."
  type        = number
  default     = 200000
}

variable "langsmith_tracing" {
  description = "Send traces to LangSmith (requires langsmith_api_key)."
  type        = bool
  default     = true
}

variable "langsmith_project" {
  type    = string
  default = "secure-rag-assistant"
}

# ---------- Secrets (write-only: never stored in Terraform state) ----------
variable "groq_api_key" {
  description = "Groq API key. Pass with TF_VAR_groq_api_key; bump secrets_version to rotate."
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "langsmith_api_key" {
  description = "LangSmith API key (optional). Pass with TF_VAR_langsmith_api_key."
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "secrets_version" {
  description = "Increment to rewrite every secret in SSM (and regenerate the JWT and Qdrant keys)."
  type        = number
  default     = 1
}

# ---------- Alerts and budgets ----------
variable "alert_email" {
  description = "Receives CloudWatch alarms (via SNS) and AWS Budgets alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "monthly_budget_usd" {
  description = "AWS Budgets limit for the whole account (alerts at 80% actual, 100% forecast)."
  type        = number
  default     = 60
}

variable "daily_llm_cost_alarm_usd" {
  description = "Alarm when the app's metered LLM cost exceeds this over a day."
  type        = number
  default     = 1
}

variable "token_spike_per_5min" {
  description = "Alarm when total tokens in 5 minutes exceed this (runaway loop or abuse)."
  type        = number
  default     = 100000
}

variable "p95_latency_alarm_ms" {
  type    = number
  default = 15000
}

variable "error_rate_alarm_percent" {
  type    = number
  default = 5
}

variable "access_denied_per_5min" {
  description = "Alarm when cross-department requests spike (someone probing for restricted data)."
  type        = number
  default     = 25
}

# ---------- GitHub Actions ----------
variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy via OIDC."
  type        = string
}

variable "github_environment" {
  description = "GitHub Actions environment used by the deploy job."
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the token.actions.githubusercontent.com provider."
  type        = bool
  default     = true
}
