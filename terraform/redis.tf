resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name}-redis-subnets"
  subnet_ids = data.aws_subnets.public.ids

  tags = local.common_tags
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis-sg"
  description = "Allow Redis traffic only from ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${local.name}-redis"
  description                = "Redis cache for ${local.name} API"
  engine                     = "redis"
  engine_version             = var.redis_engine_version
  node_type                  = var.cache_node_type
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.redis.id]
  automatic_failover_enabled = false
  multi_az_enabled           = false
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  apply_immediately          = true
  auto_minor_version_upgrade = true
  snapshot_retention_limit   = 0

  tags = local.common_tags
}
