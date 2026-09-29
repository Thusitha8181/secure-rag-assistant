locals {
  repositories = toset(["backend", "ingest", "frontend"])
}

resource "aws_ecr_repository" "this" {
  for_each = local.repositories

  name                 = "${var.project}/${each.key}"
  image_tag_mutability = "MUTABLE" # CI pushes an immutable git-SHA tag plus a moving "latest"
  force_delete         = true      # so `make destroy` works with images still present

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 15 most recent images (enough to roll back)"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 15
      }
      action = { type = "expire" }
    }]
  })
}
