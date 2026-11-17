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
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "Requests"
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.api.arn_suffix]
          ]
          period = 60
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 6
        width  = 8
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
        x      = 16
        y      = 6
        width  = 8
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
  alarm_description   = "Average ECS CPU utilization is above 70 percent for two minutes"
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
  threshold           = var.max_capacity
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}
