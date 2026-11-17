resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db-subnets"
  subnet_ids = data.aws_subnets.public.ids

  tags = local.common_tags
}

resource "aws_db_instance" "postgres" {
  identifier                 = "${local.name}-postgres"
  engine                     = "postgres"
  instance_class             = var.db_instance_class
  allocated_storage          = 20
  max_allocated_storage      = 20
  storage_type               = "gp3"
  storage_encrypted          = true
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = var.db_password
  port                       = 5432
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.db.id]
  publicly_accessible        = false
  multi_az                   = false
  backup_retention_period    = 0
  deletion_protection        = false
  skip_final_snapshot        = true
  auto_minor_version_upgrade = true
  apply_immediately          = true

  tags = local.common_tags
}
