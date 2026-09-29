# Persistent storage for Qdrant. The collection is derived data (re-created by the ingestion task),
# so EFS durability is a convenience rather than the system of record.

resource "aws_efs_file_system" "qdrant" {
  creation_token  = "${local.name}-qdrant"
  encrypted       = true
  throughput_mode = "elastic"

  tags = { Name = "${local.name}-qdrant" }
}

resource "aws_efs_mount_target" "qdrant" {
  count = length(aws_subnet.public)

  file_system_id  = aws_efs_file_system.qdrant.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "qdrant" {
  file_system_id = aws_efs_file_system.qdrant.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/qdrant"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }

  tags = { Name = "${local.name}-qdrant" }
}

data "aws_iam_policy_document" "efs" {
  statement {
    sid       = "QdrantTaskOnly"
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [aws_efs_file_system.qdrant.arn]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.qdrant_task.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.qdrant.arn]
    }
  }

  statement {
    sid       = "DenyUnencryptedTransport"
    effect    = "Deny"
    actions   = ["*"]
    resources = [aws_efs_file_system.qdrant.arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_efs_file_system_policy" "qdrant" {
  file_system_id = aws_efs_file_system.qdrant.id
  policy         = data.aws_iam_policy_document.efs.json
}
