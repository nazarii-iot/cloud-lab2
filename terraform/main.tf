data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = var.project_name
  common_tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}
