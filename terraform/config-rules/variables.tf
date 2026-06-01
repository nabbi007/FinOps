#=============================================================================
# Cost Detective AWS FinOps — Config Rules Module Variables
#=============================================================================
# Input variables for the Config Rules module. These allow customization
# of the AWS region, required tag keys, and S3 bucket naming without
# modifying the core Terraform configuration.
#=============================================================================

# ---------------------------------------------------------------------------
# region — AWS Region for Resource Deployment
# ---------------------------------------------------------------------------
# The AWS region where the Config recorder, delivery channel, S3 bucket,
# and Config rules will be created. AWS Config is a regional service, so
# each region you want to monitor requires its own recorder.
#
# Note: If include_global_resource_types is true in the recorder config,
# only enable it in ONE region to avoid duplicate recordings of IAM and
# other global resources. This project is single-region (eu-west-1).
variable "region" {
  description = "AWS region for Config recorder and related resources."
  type        = string
  default     = "eu-west-1"
}

# ---------------------------------------------------------------------------
# required_tags — Mandatory Tag Keys for Compliance
# ---------------------------------------------------------------------------
# A list of tag keys that must be present on evaluated resources (EC2
# instances in this module). Resources missing any of these tags will be
# marked as NON_COMPLIANT by the Config rule.
#
# For FinOps, the two most critical tags are:
#   - CostCenter:   Maps resources to business units for chargeback/showback
#   - Environment:  Distinguishes dev/staging/prod for cost segmentation
#
# The AWS managed rule "REQUIRED_TAGS" supports up to 6 tag keys.
# If you need more, consider using a custom Lambda-backed Config rule.
variable "required_tags" {
  description = "List of tag keys that must be present on evaluated resources. Max 6 keys supported by the managed rule."
  type        = list(string)
  default     = ["CostCenter", "Environment"]

  validation {
    condition     = length(var.required_tags) >= 1 && length(var.required_tags) <= 6
    error_message = "The required_tags list must contain between 1 and 6 tag keys (AWS managed rule limitation)."
  }
}

# ---------------------------------------------------------------------------
# config_bucket_prefix — S3 Bucket Name Prefix
# ---------------------------------------------------------------------------
# The prefix for the S3 bucket that stores AWS Config delivery snapshots
# and configuration history. The full bucket name is constructed as:
#
#   {config_bucket_prefix}-{aws_account_id}
#
# Appending the account ID ensures bucket name uniqueness across AWS
# accounts (S3 bucket names are globally unique). The prefix should be
# lowercase and use only hyphens as separators (no underscores or dots)
# to comply with S3 naming rules.
variable "config_bucket_prefix" {
  description = "Prefix for the S3 bucket name used by the Config delivery channel. Account ID is appended automatically."
  type        = string
  default     = "cost-detective-config"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.config_bucket_prefix))
    error_message = "The config_bucket_prefix must contain only lowercase letters, numbers, and hyphens, and must start/end with a letter or number."
  }
}
