# =============================================================================
# Cost Detective — Lambda Scanner Module · Outputs
# =============================================================================
# Values exported for reference by other modules, dashboards, or CI/CD.
# =============================================================================

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------
output "lambda_arn" {
  description = "ARN of the Cost Detective scanner Lambda function."
  value       = aws_lambda_function.scanner.arn
}

output "lambda_function_name" {
  description = "Name of the Lambda function (useful for invoke commands)."
  value       = aws_lambda_function.scanner.function_name
}

# -----------------------------------------------------------------------------
# EventBridge Rule
# -----------------------------------------------------------------------------
output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule that triggers the scanner."
  value       = aws_cloudwatch_event_rule.weekly_scan.arn
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule (useful for enable/disable commands)."
  value       = aws_cloudwatch_event_rule.weekly_scan.name
}

# -----------------------------------------------------------------------------
# SNS Topic
# -----------------------------------------------------------------------------
output "sns_topic_arn" {
  description = "ARN of the SNS topic where scanner findings are published."
  value       = aws_sns_topic.scanner_alerts.arn
}
