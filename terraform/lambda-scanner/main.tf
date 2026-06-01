# =============================================================================
# Cost Detective — Lambda Scanner Module
# =============================================================================
# Deploys a serverless scanner that runs on a weekly schedule (EventBridge) to
# detect idle and orphaned AWS resources that are silently costing money:
#
#   • Unattached EBS volumes
#   • Unassociated Elastic IPs
#   • Idle EC2 instances (low CPU utilisation over 14 days)
#
# Architecture:
#
#   EventBridge Rule (rate)  ──►  Lambda Function  ──►  SNS Topic  ──►  Email
#                                      │
#                                      ├── EC2 DescribeVolumes / DescribeAddresses
#                                      └── CloudWatch GetMetricStatistics
#
# All findings are published to an SNS topic and returned as structured JSON.
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform & Provider Configuration
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "CostDetective"
      ManagedBy = "Terraform"
      Module    = "lambda-scanner"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

# Current AWS account and region — used to build ARNs deterministically.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Package the Python handler into a zip archive for Lambda deployment.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/build/lambda_function.zip"
}

# -----------------------------------------------------------------------------
# IAM Role — Lambda Execution Role
# -----------------------------------------------------------------------------
# Grants the Lambda service permission to assume this role, and attaches a
# least-privilege inline policy for the specific API calls the scanner needs.

resource "aws_iam_role" "lambda_exec" {
  name        = "cost-detective-scanner-role"
  description = "Execution role for the Cost Detective Lambda scanner"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Purpose = "LambdaScannerExecution"
  }
}

# -----------------------------------------------------------------------------
# IAM Inline Policy — Least-Privilege Permissions
# -----------------------------------------------------------------------------
# The scanner needs:
#   • ec2:Describe*          — list volumes, EIPs, instances
#   • cloudwatch:GetMetric*  — fetch CPU utilisation for idle-instance detection
#   • sns:Publish            — send the report to the notification topic
#   • logs:*                 — write execution logs to CloudWatch Logs

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "cost-detective-scanner-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2ReadAccess"
        Effect = "Allow"
        Action = [
          "ec2:DescribeVolumes",
          "ec2:DescribeAddresses",
          "ec2:DescribeInstances",
          "ec2:DescribeRegions"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData"
        ]
        Resource = "*"
      },
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.scanner_alerts.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------
# The scanner function is written in Python 3.12 and performs three checks:
#   1. Unattached EBS volumes  (volumes in "available" state)
#   2. Unassociated Elastic IPs (EIPs without an instance association)
#   3. Idle EC2 instances       (average CPU < threshold over 14 days)
#
# Results are published to SNS and returned as JSON.

resource "aws_lambda_function" "scanner" {
  function_name = "cost-detective-scanner"
  description   = "Weekly scan for idle/orphaned AWS resources (FinOps waste detection)"

  # Deployment package built by the archive_file data source.
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  # 5-minute timeout — the scanner iterates over multiple API calls and may
  # need extra time in accounts with many resources.
  timeout     = 300
  memory_size = 256

  role = aws_iam_role.lambda_exec.arn

  # Environment variables consumed by the Python handler.
  environment {
    variables = {
      SNS_TOPIC_ARN      = aws_sns_topic.scanner_alerts.arn
      IDLE_CPU_THRESHOLD = tostring(var.idle_cpu_threshold)
    }
  }

  tags = {
    Purpose = "FinOpsWasteDetection"
  }

  depends_on = [
    aws_iam_role_policy.lambda_permissions
  ]
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group — Lambda Logs
# -----------------------------------------------------------------------------
# Explicitly create the log group so we can control retention and avoid orphan
# log groups when the stack is destroyed.

resource "aws_cloudwatch_log_group" "scanner_logs" {
  name              = "/aws/lambda/${aws_lambda_function.scanner.function_name}"
  retention_in_days = 30

  tags = {
    Purpose = "LambdaScannerLogs"
  }
}

# -----------------------------------------------------------------------------
# EventBridge (CloudWatch Events) — Weekly Schedule
# -----------------------------------------------------------------------------
# Triggers the scanner on a configurable schedule (default: once per week).

resource "aws_cloudwatch_event_rule" "weekly_scan" {
  name                = "cost-detective-weekly-scan"
  description         = "Triggers the Cost Detective scanner Lambda on a recurring schedule"
  schedule_expression = var.scan_schedule

  tags = {
    Purpose = "ScheduledFinOpsScan"
  }
}

# Connect the EventBridge rule to the Lambda function.
resource "aws_cloudwatch_event_target" "invoke_scanner" {
  rule      = aws_cloudwatch_event_rule.weekly_scan.name
  target_id = "CostDetectiveScannerTarget"
  arn       = aws_lambda_function.scanner.arn
}

# Grant EventBridge permission to invoke the Lambda function.
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_scan.arn
}

# -----------------------------------------------------------------------------
# SNS Topic — Scanner Notifications
# -----------------------------------------------------------------------------
# The scanner publishes its findings here.  Subscribe an email address (or
# any supported protocol) to receive alerts when waste is detected.

resource "aws_sns_topic" "scanner_alerts" {
  name         = "cost-detective-scanner-alerts"
  display_name = "Cost Detective Scanner Alerts"

  tags = {
    Purpose = "FinOpsAlerts"
  }
}

# Email subscription — the recipient must confirm the subscription via the
# link in the initial SNS confirmation email.
resource "aws_sns_topic_subscription" "email_alert" {
  topic_arn = aws_sns_topic.scanner_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
