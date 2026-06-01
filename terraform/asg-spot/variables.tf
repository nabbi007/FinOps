# =============================================================================
# Cost Detective — ASG Spot Module · Variables
# =============================================================================
# All input variables for the asg-spot module.  Sensible defaults are provided
# so the module can be applied with zero overrides for a quick dev deployment.
# =============================================================================

# -----------------------------------------------------------------------------
# AWS Region
# -----------------------------------------------------------------------------
variable "region" {
  description = "AWS region where all resources will be created."
  type        = string
  default     = "eu-west-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.region))
    error_message = "Region must be a valid AWS region identifier (e.g. eu-west-1)."
  }
}

# -----------------------------------------------------------------------------
# Instance Configuration
# -----------------------------------------------------------------------------
variable "instance_type" {
  description = <<-EOT
    Default EC2 instance type for the launch template.  The ASG mixed-instances
    policy may override this with additional types to broaden Spot pool diversity.
  EOT
  type        = string
  default     = "t3.micro"
}

# -----------------------------------------------------------------------------
# Auto Scaling Group — Capacity Settings
# -----------------------------------------------------------------------------
variable "min_size" {
  description = "Minimum number of instances the ASG will maintain."
  type        = number
  default     = 1

  validation {
    condition     = var.min_size >= 0
    error_message = "min_size must be zero or a positive integer."
  }
}

variable "max_size" {
  description = "Maximum number of instances the ASG is allowed to scale to."
  type        = number
  default     = 4

  validation {
    condition     = var.max_size >= 1
    error_message = "max_size must be at least 1."
  }
}

variable "desired_capacity" {
  description = <<-EOT
    Initial desired number of instances.  Must satisfy:
      min_size <= desired_capacity <= max_size
    Terraform ignores subsequent changes to avoid conflicts with auto-scaling.
  EOT
  type        = number
  default     = 2
}

# -----------------------------------------------------------------------------
# Spot Configuration
# -----------------------------------------------------------------------------
variable "spot_percentage" {
  description = <<-EOT
    Percentage of capacity ABOVE the on-demand base that should use Spot
    instances.  For example, 30 means 30 % Spot / 70 % On-Demand above base.
    The on-demand base itself is always 1 (hardcoded in main.tf).
  EOT
  type        = number
  default     = 30

  validation {
    condition     = var.spot_percentage >= 0 && var.spot_percentage <= 100
    error_message = "spot_percentage must be between 0 and 100 (inclusive)."
  }
}

# -----------------------------------------------------------------------------
# Tags
# -----------------------------------------------------------------------------
variable "tags" {
  description = <<-EOT
    Map of tags applied to all resources.  At a minimum the following keys are
    recommended for FinOps cost-allocation reports:
      • CostCenter
      • Environment
      • Project
  EOT
  type        = map(string)
  default = {
    CostCenter  = "FinOps"
    Environment = "development"
    Project     = "CostDetective"
  }
}
