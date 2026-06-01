#=============================================================================
# Cost Detective AWS FinOps — Config Rules Module Outputs
#=============================================================================
# Outputs expose key resource identifiers from the Config Rules module so
# that other modules (e.g., dashboards, alerting, remediation) can
# reference them without hard-coding ARNs or IDs.
#=============================================================================

# ---------------------------------------------------------------------------
# config_rule_arn — Config Rule ARN
# ---------------------------------------------------------------------------
# The Amazon Resource Name (ARN) of the required-tags Config rule.
# This ARN can be used to:
#   - Set up EventBridge rules that trigger on compliance state changes
#   - Create CloudWatch dashboards showing compliance metrics
#   - Reference the rule in remediation automation (SSM Automation docs)
#   - Build cross-module dependencies for FinOps reporting
output "config_rule_arn" {
  description = "ARN of the required-tags AWS Config rule for compliance monitoring."
  value       = aws_config_config_rule.required_tags.arn
}

# ---------------------------------------------------------------------------
# config_recorder_id — Configuration Recorder ID
# ---------------------------------------------------------------------------
# The ID (name) of the AWS Config configuration recorder. This can be
# used to:
#   - Check recorder status in other modules or scripts
#   - Reference the recorder when adding additional Config rules
#   - Manage recorder lifecycle (start/stop) via automation
output "config_recorder_id" {
  description = "The ID (name) of the AWS Config configuration recorder."
  value       = aws_config_configuration_recorder.main.id
}

# ---------------------------------------------------------------------------
# s3_bucket_name — Config Delivery S3 Bucket Name
# ---------------------------------------------------------------------------
# The name of the S3 bucket used by the Config delivery channel to store
# configuration snapshots and history files. This can be used to:
#   - Set up lifecycle policies for cost-effective long-term storage
#   - Configure cross-account access for centralized Config aggregation
#   - Reference in data analysis pipelines (Athena, QuickSight)
#   - Add additional bucket policies or notifications
output "s3_bucket_name" {
  description = "Name of the S3 bucket used by the AWS Config delivery channel."
  value       = aws_s3_bucket.config_delivery.id
}
