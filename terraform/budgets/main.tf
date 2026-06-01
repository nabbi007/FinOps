#=============================================================================
# Cost Detective AWS FinOps — Budget Monitoring Module
#=============================================================================
# This module provisions an AWS Budget with SNS-based email alerting.
# It creates:
#   1. An SNS topic dedicated to budget alert notifications
#   2. An email subscription so the FinOps team receives alerts
#   3. A monthly COST budget with two notification thresholds:
#      - ACTUAL spend exceeding 80% of the limit
#      - FORECASTED spend exceeding 100% of the limit
#
# Usage:
#   module "budgets" {
#     source      = "./terraform/budgets"
#     region      = "eu-west-1"
#     alert_email = "finops-team@example.com"
#     budget_limit = 50
#     tags        = { Project = "CostDetective", Environment = "production" }
#   }
#=============================================================================

# ---------------------------------------------------------------------------
# Terraform Configuration
# ---------------------------------------------------------------------------
# Pin the AWS provider to the 5.x major version to ensure compatibility
# and prevent unexpected breaking changes from provider upgrades.
terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# AWS Provider
# ---------------------------------------------------------------------------
# The region is configurable via the `region` variable so this module can be
# deployed to any AWS region without code changes.
provider "aws" {
  region = var.region
}

# ---------------------------------------------------------------------------
# SNS Topic — Budget Alert Notifications
# ---------------------------------------------------------------------------
# A dedicated SNS topic that serves as the notification channel for all
# budget alerts. Keeping a separate topic (rather than reusing an existing
# one) makes it easy to manage subscriptions and permissions independently.
resource "aws_sns_topic" "budget_alerts" {
  name = "cost-detective-budget-alerts"

  tags = merge(var.tags, {
    Name    = "cost-detective-budget-alerts"
    Purpose = "FinOps budget alert notifications"
  })
}

# ---------------------------------------------------------------------------
# SNS Topic Subscription — Email Delivery
# ---------------------------------------------------------------------------
# Subscribes the designated alert email address to the budget SNS topic.
# NOTE: After the first `terraform apply`, the email recipient must confirm
# the subscription by clicking the link in the AWS SNS confirmation email.
resource "aws_sns_topic_subscription" "budget_alert_email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------------------
# AWS Budget — Monthly Cost Budget
# ---------------------------------------------------------------------------
# Creates a monthly cost budget with a configurable spending limit.
# Two notification rules are attached:
#
#   1. ACTUAL > 80%  — Warns the team when real spending is approaching the
#      budget ceiling, giving time to investigate and take corrective action.
#
#   2. FORECASTED > 100% — Alerts the team when AWS projects that spending
#      will exceed the budget by month-end, enabling proactive intervention
#      even if current spend is still within limits.
#
# Cost filters restrict the budget scope to specific linked accounts when
# operating in an AWS Organizations multi-account setup.
resource "aws_budgets_budget" "monthly_cost" {
  name         = "cost-detective-monthly-budget"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = var.budget_limit
  limit_unit   = "USD"

  # ---------- Notification 1: Actual spend exceeds 80% ----------
  # This is an early-warning threshold. When actual costs reach 80% of the
  # monthly budget, the team is notified so they can review spending trends
  # and decide whether to scale back resources or adjust the budget.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  # ---------- Notification 2: Forecasted spend exceeds 100% ----------
  # This forward-looking threshold leverages AWS's cost forecasting engine.
  # If the projected end-of-month spend is expected to surpass the budget,
  # the team receives an alert — even if current spend is still under 80%.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  # ---------- Cost Filters ----------
  # Filter by LinkedAccount to scope this budget to specific AWS accounts.
  # This is especially useful in AWS Organizations where a management
  # account creates budgets that track spending in member accounts.
  cost_filter {
    name   = "LinkedAccount"
    values = [data.aws_caller_identity.current.account_id]
  }

  tags = merge(var.tags, {
    Name    = "cost-detective-monthly-budget"
    Purpose = "Monthly cost tracking and alerting"
  })
}

# ---------------------------------------------------------------------------
# Data Source — Current AWS Account Identity
# ---------------------------------------------------------------------------
# Retrieves the account ID of the caller so we can use it in the
# LinkedAccount cost filter above without hard-coding account numbers.
data "aws_caller_identity" "current" {}
