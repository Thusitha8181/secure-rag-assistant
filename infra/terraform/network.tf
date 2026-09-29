# Two public subnets and no NAT gateway (saves ~$33/month per AZ). Tasks get public IPs so they
# can pull images and call Groq, but security groups admit traffic only from the ALB or from
# sibling services - nothing on the internet can reach a task directly.

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # Cloud Map private DNS and EFS mount names need both

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Strip the default security group's allow-all rules so nothing uses it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
}

# ---------- Security groups ----------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "frontend" {
  name        = "${local.name}-frontend"
  description = "Next.js tasks, reachable from the ALB only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "backend" {
  name        = "${local.name}-backend"
  description = "FastAPI tasks, reachable from the ALB and the frontend only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "ingest" {
  name        = "${local.name}-ingest"
  description = "One-off ingestion task (no inbound)"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "qdrant" {
  name        = "${local.name}-qdrant"
  description = "Qdrant, reachable from the backend and the ingestion task only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "efs" {
  name        = "${local.name}-efs"
  description = "EFS mount targets, reachable from Qdrant only"
  vpc_id      = aws_vpc.this.id
}

locals {
  alb_ports = local.https ? [80, 443] : [80]

  # Each entry: which security group accepts which port from which source group.
  internal_ingress = {
    frontend_from_alb  = { sg = aws_security_group.frontend.id, port = 3000, from = aws_security_group.alb.id }
    backend_from_alb   = { sg = aws_security_group.backend.id, port = 8000, from = aws_security_group.alb.id }
    backend_from_web   = { sg = aws_security_group.backend.id, port = 8000, from = aws_security_group.frontend.id }
    qdrant_from_api    = { sg = aws_security_group.qdrant.id, port = 6333, from = aws_security_group.backend.id }
    qdrant_from_ingest = { sg = aws_security_group.qdrant.id, port = 6333, from = aws_security_group.ingest.id }
    efs_from_qdrant    = { sg = aws_security_group.efs.id, port = 2049, from = aws_security_group.qdrant.id }
  }

  # Tasks need outbound internet for ECR, Docker Hub, Groq, LangSmith and AWS APIs.
  egress_groups = {
    alb      = aws_security_group.alb.id
    frontend = aws_security_group.frontend.id
    backend  = aws_security_group.backend.id
    ingest   = aws_security_group.ingest.id
    qdrant   = aws_security_group.qdrant.id
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb" {
  for_each = {
    for pair in setproduct(local.alb_ports, var.allowed_ingress_cidrs) :
    "${pair[0]}-${pair[1]}" => { port = pair[0], cidr = pair[1] }
  }

  security_group_id = aws_security_group.alb.id
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr
}

resource "aws_vpc_security_group_ingress_rule" "internal" {
  for_each = local.internal_ingress

  security_group_id            = each.value.sg
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = each.value.from
  description                  = each.key
}

resource "aws_vpc_security_group_egress_rule" "all" {
  for_each = local.egress_groups

  security_group_id = each.value
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
