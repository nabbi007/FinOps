#=============================================================================
# Cost Detective AWS FinOps — Config Rules Module
#=============================================================================
# This module sets up AWS Config to continuously evaluate resource compliance
# against tagging policies. It creates:
#   1. An S3 bucket for Config delivery channel snapshots
#   2. An IAM role granting AWS Config the necessary permissions
#   3. A Config recorder and delivery channel
#   4. A managed Config rule ("required-tags") that checks EC2 instances
#      for mandatory cost-allocation tags (CostCenter, Environment)
#
# Why tagging matters for FinOps:
#   - Untagged resources are invisible in cost allocation reports
#   - Missing CostCenter tags make chargeback/showback impossible
#   - Missing Environment tags prevent per-environment cost analysis
#
# Usage:
#   module "config_rules" {
#     source               = "./terraform/config-rules"
#     region               = "eu-west-1"
#     required_tags        = ["CostCenter", "Environment"]
#     config_bucket_prefix = "cost-detective-config"
#   }
#=============================================================================

# ---------------------------------------------------------------------------
# Terraform Configuration
# ---------------------------------------------------------------------------
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
provider "aws" {
  region = var.region
}

# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------
# Retrieve the current AWS account ID and region for use in IAM policies
# and resource naming. This avoids hard-coding account-specific values.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ===========================================================================
#  S3 BUCKET — Config Delivery Channel Storage
# ===========================================================================
# AWS Config requires an S3 bucket to store configuration snapshots and
# history files. This bucket is dedicated to Config to keep permissions
# tightly scoped and simplify lifecycle management.
resource "aws_s3_bucket" "config_delivery" {
  bucket = "${var.config_bucket_prefix}-${data.aws_caller_identity.current.account_id}"

  # Force destroy allows Terraform to delete the bucket even when it contains
  # objects. This is useful for dev/test environments but should be set to
  # false in production to prevent accidental data loss.
  force_destroy = true

  tags = {
    Name      = "${var.config_bucket_prefix}-${data.aws_caller_identity.current.account_id}"
    Purpose   = "AWS Config delivery channel storage"
    ManagedBy = "Terraform"
  }
}

# ---------------------------------------------------------------------------
# S3 Bucket Policy — Allow AWS Config to Write
# ---------------------------------------------------------------------------
# This policy grants the AWS Config service principal permission to:
#   1. Check the bucket's ACL (GetBucketAcl) to verify write access
#   2. Write configuration snapshots and history (PutObject) with the
#      required bucket-owner-full-control ACL
#
# The policy restricts PutObject to a specific key prefix that matches
# the Config delivery channel's expected path structure.
resource "aws_s3_bucket_policy" "config_delivery_policy" {
  bucket = aws_s3_bucket.config_delivery.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowConfigBucketAccess"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.config_delivery.arn
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        Sid    = "AllowConfigBucketDelivery"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.config_delivery.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/Config/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"      = "bucket-owner-full-control"
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# ===========================================================================
#  IAM ROLE — AWS Config Service Role
# ===========================================================================
# AWS Config needs an IAM role to:
#   - Read the configuration of AWS resources it monitors
#   - Write configuration snapshots to the S3 delivery bucket
#   - Evaluate Config rules against recorded resource states
#
# The trust policy restricts role assumption to the Config service only.
resource "aws_iam_role" "config_role" {
  name = "cost-detective-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowConfigAssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name      = "cost-detective-config-role"
    Purpose   = "IAM role for AWS Config service"
    ManagedBy = "Terraform"
  }
}

# ---------------------------------------------------------------------------
# IAM Role Policy Attachment — AWS Managed Config Policy
# ---------------------------------------------------------------------------
# Attaches the AWS-managed policy that grants Config the permissions it
# needs to describe resources, deliver configuration items, and evaluate
# rules. Using the managed policy is preferred over a custom policy because
# AWS automatically updates it when new resource types are added.
resource "aws_iam_role_policy_attachment" "config_policy" {
  role       = aws_iam_role.config_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

# ===========================================================================
#  AWS CONFIG RECORDER
# ===========================================================================
# The configuration recorder captures resource configuration changes.
# It is set to record ALL supported resource types, which ensures
# comprehensive visibility. For cost optimization in large environments,
# you could restrict recording to specific resource types.
resource "aws_config_configuration_recorder" "main" {
  name     = "cost-detective-config-recorder"
  role_arn = aws_iam_role.config_role.arn

  recording_group {
    # Record all supported resource types in this region.
    # Set to false and specify resource_types list to limit scope.
    all_supported = true

    # Include global resources (IAM users, roles, policies) in recording.
    # Only enable this in ONE region to avoid duplicate recordings.
    include_global_resource_types = true
  }
}

# ---------------------------------------------------------------------------
# AWS Config Delivery Channel
# ---------------------------------------------------------------------------
# The delivery channel specifies where AWS Config sends configuration
# snapshots and history. Configuration snapshots are delivered to the
# S3 bucket at the frequency specified (default: 24 hours).
resource "aws_config_delivery_channel" "main" {
  name           = "cost-detective-config-delivery"
  s3_bucket_name = aws_s3_bucket.config_delivery.id

  # Snapshot delivery frequency — how often Config exports a full
  # configuration snapshot to S3. Options:
  #   One_Hour | Three_Hours | Six_Hours | Twelve_Hours | TwentyFour_Hours
  snapshot_delivery_properties {
    delivery_frequency = "TwentyFour_Hours"
  }

  # The recorder must exist AND the bucket policy must already grant Config
  # write access before the delivery channel is created — otherwise AWS
  # rejects PutDeliveryChannel with "Insufficient delivery policy".
  depends_on = [
    aws_config_configuration_recorder.main,
    aws_s3_bucket_policy.config_delivery_policy,
  ]
}

# ===========================================================================
#  CONFIG RULE — Required Tags (Managed Rule)
# ===========================================================================
# This rule uses the AWS-managed "required-tags" rule to check that EC2
# instances have the mandatory cost-allocation tags. Non-compliant
# resources will appear in the AWS Config dashboard and can trigger
# remediation actions.
#
# The rule checks for two tags critical to FinOps:
#   1. CostCenter  — Enables chargeback/showback to business units
#   2. Environment — Enables per-environment cost analysis (dev/staging/prod)
#
# AWS Config evaluates this rule whenever a matching resource's
# configuration changes, providing near-real-time compliance monitoring.
resource "aws_config_config_rule" "required_tags" {
  name        = "cost-detective-required-tags"
  description = "Checks that EC2 instances have required cost-allocation tags (CostCenter, Environment) for FinOps tracking."

  source {
    # Use the AWS-managed rule identifier for tag checking.
    # Managed rules are maintained by AWS and require no custom Lambda.
    owner             = "AWS"
    source_identifier = "REQUIRED_TAGS"
  }

  # --- Scope: Limit evaluation to EC2 instances ---
  # Only EC2 instances are checked by this rule. To check additional
  # resource types, add their compliance resource types to the list.
  scope {
    compliance_resource_types = ["AWS::EC2::Instance"]
  }

  # --- Input Parameters: Define which tags are required ---
  # The REQUIRED_TAGS managed rule accepts up to 6 tag key/value pairs as
  # tag1Key..tag6Key. We map however many keys are supplied in var.required_tags
  # (1-6, enforced by the variable validation) so the rule stays in sync with
  # the variable without hardcoding indices. Keys only — any value is accepted.
  input_parameters = jsonencode({
    for idx, key in var.required_tags : "tag${idx + 1}Key" => key
  })

  # Ensure the recorder is active before creating rules, otherwise
  # AWS Config will reject the rule creation.
  depends_on = [aws_config_configuration_recorder_status.main]
}

# ===========================================================================
#  CONFIG RECORDER STATUS — Enable Recording
# ===========================================================================
# The recorder must be explicitly enabled after creation. This resource
# manages the on/off state of the configuration recorder. Setting
# is_enabled = true starts continuous recording of resource configurations.
resource "aws_config_configuration_recorder_status" "main" {
  name       = aws_config_configuration_recorder.main.name
  is_enabled = true

  # The delivery channel must exist before the recorder can be enabled,
  # because Config needs a destination for the recorded data.
  depends_on = [aws_config_delivery_channel.main]
}
