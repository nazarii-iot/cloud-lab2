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

  validation {
    condition     = var.max_capacity >= var.min_capacity
    error_message = "max_capacity must be greater than or equal to min_capacity."
  }
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

  validation {
    condition     = var.requests_per_target > 0
    error_message = "requests_per_target must be greater than zero."
  }
}

variable "scale_out_cooldown_seconds" {
  type    = number
  default = 60
}

variable "scale_in_cooldown_seconds" {
  type    = number
  default = 180
}

variable "cache_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "redis_engine_version" {
  type    = string
  default = "7.1"
}

variable "dashboard_annotations" {
  type = list(object({
    label = string
    value = string
  }))
  default = []
}

variable "enable_redis_cache" {
  type    = bool
  default = true
}

variable "max_tasks_alarm_threshold" {
  type    = number
  default = 4

  validation {
    condition     = var.max_tasks_alarm_threshold >= 1
    error_message = "max_tasks_alarm_threshold must be at least 1."
  }
}