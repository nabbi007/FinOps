#=============================================================================
# Cost Detective AWS FinOps — Budget Module Outputs
#=============================================================================
# Outputs expose key resource identifiers so that other modules or the root
# Terraform configuration can reference them. For example, the budget_id can
# be used for cross-module dependencies, and the sns_topic_arn can be shared
# with other modules that need to publish to the same alert channel.
#=============================================================================

# ---------------------------------------------------------------------------
# budget_id — AWS Budget Identifier
# ---------------------------------------------------------------------------
# The unique identifier of the monthly cost budget. This can be used to:
#   - Reference the budget in other Terraform modules or resources
#   - Look up the budget in the AWS Budgets console
#   - Pass to monitoring or dashboard modules for unified cost visibility
output "budget_id" {
  description = "The unique identifier of the monthly cost budget."
  value       = aws_budgets_budget.monthly_cost.id
}

# ---------------------------------------------------------------------------
# sns_topic_arn — Budget Alert SNS Topic ARN
# ---------------------------------------------------------------------------
# The Amazon Resource Name (ARN) of the SNS topic used for budget alerts.
# This ARN can be:
#   - Shared with other modules that need to send notifications to the same
#     topic (e.g., anomaly detection, cost optimization recommendations)
#   - Used to add additional subscriptions (Slack webhook, PagerDuty, etc.)
#   - Referenced in IAM policies to grant publish permissions
output "sns_topic_arn" {
  description = "The ARN of the SNS topic used for budget alert notifications."
  value       = aws_sns_topic.budget_alerts.arn
}
