variable "aws_region" {
  type    = string
  default = "eu-north-1"
}

variable "project_name" {
  type    = string
  default = "cloud-lab2"
}

variable "api_image_tag" {
  type    = string
  default = "latest"
}

variable "alert_email" {
  type    = string
  default = ""
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "min_capacity" {
  type    = number
  default = 1
}

variable "max_capacity" {
  type    = number
  default = 4
}

variable "cpu_target_value" {
  type    = number
  default = 60
}

variable "db_name" {
  type    = string
  default = "clouddb"
}

variable "db_username" {
  type    = string
  default = "clouduser"
}

variable "db_password" {
  type      = string
  sensitive = true

  validation {
    condition     = can(regex("^[A-Za-z0-9]{20,}$", var.db_password))
    error_message = "DB password must contain at least 20 alphanumeric characters."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "requests_per_target" {
  type    = number
  default = 80
}
