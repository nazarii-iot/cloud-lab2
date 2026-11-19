resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "ECS desired and running tasks"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["ECS/ContainerInsights", "DesiredTaskCount", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.api.name],
            [".", "RunningTaskCount", ".", ".", ".", "."]
          ]
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "ECS CPU utilization"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.api.name]
          ]
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "ALB requests and requests per target"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "Sum", label = "Requests" }],
            [".", "RequestCountPerTarget", ".", ".", "TargetGroup", aws_lb_target_group.api.arn_suffix, { stat = "Sum", label = "Requests per target" }]
          ]
          period = 60
          stat   = "Sum"
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Target response time p95"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "p95" }]
          ]
          period = 60
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Redis cache hits and misses"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "CacheHits", "CacheClusterId", tolist(aws_elasticache_replication_group.redis.member_clusters)[0], { stat = "Sum" }],
            [".", "CacheMisses", ".", ".", { stat = "Sum" }]
          ]
          period = 60
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "HTTP 5xx responses"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", "LoadBalancer", aws_lb.api.arn_suffix],
            [".", "HTTPCode_Target_5XX_Count", ".", "."]
          ]
          period = 60
          stat   = "Sum"
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 24
        height = 6
        properties = {
          title  = "RDS PostgreSQL read and write activity"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/RDS", "ReadIOPS", "DBInstanceIdentifier", aws_db_instance.postgres.identifier, { stat = "Average", label = "Read IOPS" }],
            [".", "WriteIOPS", ".", ".", { stat = "Average", label = "Write IOPS" }]
          ]
          period = 60
          annotations = {
            vertical = [
              for marker in var.dashboard_annotations : {
                label = marker.label
                value = marker.value
              }
            ]
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.name}-high-cpu"
  alarm_description   = "Average ECS CPU utilization is above 70 percent for two consecutive minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 70
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "max_tasks" {
  alarm_name          = "${local.name}-max-tasks"
  alarm_description   = "ECS desired task count has reached the configured maximum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "DesiredTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 60
  statistic           = "Maximum"
  threshold           = var.max_tasks_alarm_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "min_tasks" {
  alarm_name          = "${local.name}-min-tasks"
  alarm_description   = "Notification marker: ECS desired task count has returned to the configured minimum. ALARM state is expected for this event."
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "DesiredTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 60
  statistic           = "Minimum"
  threshold           = var.min_capacity
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "high_p95_latency" {
  alarm_name          = "${local.name}-high-p95-latency"
  alarm_description   = "ALB target response time p95 is above 500 ms for two consecutive minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95"
  threshold           = 0.5
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.api.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}
