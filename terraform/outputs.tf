# =============================================================================
# Cost Detective — Root Outputs
# =============================================================================
# Surfaces the most useful identifiers from each module after `terraform apply`.
# =============================================================================

output "budget_id" {
  description = "ID of the monthly cost budget."
  value       = module.budgets.budget_id
}

output "budget_sns_topic_arn" {
  description = "SNS topic ARN for budget alerts."
  value       = module.budgets.sns_topic_arn
}

output "config_rule_arn" {
  description = "ARN of the required-tags AWS Config rule."
  value       = module.config_rules.config_rule_arn
}

output "config_s3_bucket_name" {
  description = "S3 bucket backing the AWS Config delivery channel."
  value       = module.config_rules.s3_bucket_name
}

output "asg_name" {
  description = "Name of the Spot-enabled Auto Scaling Group."
  value       = module.asg_spot.asg_name
}

output "launch_template_id" {
  description = "Launch template ID used by the ASG."
  value       = module.asg_spot.launch_template_id
}

output "scanner_lambda_name" {
  description = "Name of the weekly waste-scanner Lambda function."
  value       = module.lambda_scanner.lambda_function_name
}

output "scanner_sns_topic_arn" {
  description = "SNS topic ARN where scanner findings are published."
  value       = module.lambda_scanner.sns_topic_arn
}
