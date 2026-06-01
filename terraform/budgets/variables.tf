#=============================================================================
# Cost Detective AWS FinOps — Budget Module Variables
#=============================================================================
# Input variables for the budgets module. These allow the module to be
# reused across environments (dev, staging, production) and AWS accounts
# without modifying the core Terraform configuration.
#=============================================================================

# ---------------------------------------------------------------------------
# region — AWS Region for Resource Deployment
# ---------------------------------------------------------------------------
# The AWS region where the SNS topic and budget will be created.
# Budgets are a global service, but the SNS topic requires a region.
# Defaults to eu-west-1 to match the project's standard region.
variable "region" {
  description = "AWS region where the SNS topic and related resources will be created."
  type        = string
  default     = "eu-west-1"
}

# ---------------------------------------------------------------------------
# alert_email — Notification Recipient
# ---------------------------------------------------------------------------
# The email address that will receive budget alert notifications via SNS.
# After the first deployment, the recipient MUST confirm the SNS subscription
# by clicking the confirmation link sent by AWS. Until confirmed, no alerts
# will be delivered.
#
# In production, consider using a distribution list (e.g., finops@company.com)
# rather than an individual's email to ensure alerts reach the whole team.
variable "alert_email" {
  description = "Email address to receive budget alert notifications. Must be confirmed after first deployment."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.alert_email))
    error_message = "The alert_email must be a valid email address (e.g., finops@example.com)."
  }
}

# ---------------------------------------------------------------------------
# budget_limit — Monthly Spending Limit (USD)
# ---------------------------------------------------------------------------
# The maximum monthly budget amount in US dollars. Alert thresholds are
# calculated as percentages of this value:
#   - 80% of $50 = $40 (ACTUAL alert fires)
#   - 100% of $50 = $50 (FORECASTED alert fires)
#
# Adjust this value based on expected workload costs per environment.
variable "budget_limit" {
  description = "Monthly budget limit in USD. Alert thresholds are percentages of this value."
  type        = string
  default     = "50"

  validation {
    condition     = can(tonumber(var.budget_limit)) && tonumber(var.budget_limit) > 0
    error_message = "The budget_limit must be a positive number representing USD amount."
  }
}

# ---------------------------------------------------------------------------
# tags — Resource Tags
# ---------------------------------------------------------------------------
# A map of tags to apply to all resources created by this module.
# Tags are critical for:
#   - Cost allocation and chargeback reporting
#   - Resource identification and ownership tracking
#   - Compliance and governance automation
#
# Example:
#   tags = {
#     Project     = "CostDetective"
#     Environment = "production"
#     Team        = "FinOps"
#     ManagedBy   = "Terraform"
#   }
variable "tags" {
  description = "Map of tags to apply to all resources created by this module."
  type        = map(string)
  default     = {}
}
