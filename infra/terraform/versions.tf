terraform {
  # 1.11+ for write-only attributes, which keep secrets out of the state file.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Local state by default. For shared use, switch to S3 with native locking:
  # backend "s3" {
  #   bucket       = "my-tf-state-bucket"
  #   key          = "secure-rag-assistant/terraform.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}
