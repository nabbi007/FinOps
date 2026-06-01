# =============================================================================
# Cost Detective — Lambda Scanner Module · Variables
# =============================================================================
# Input variables for the serverless waste-detection scanner.
# =============================================================================

# -----------------------------------------------------------------------------
# AWS Region
# -----------------------------------------------------------------------------
variable "region" {
  description = "AWS region where the Lambda and supporting resources are deployed."
  type        = string
  default     = "eu-west-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.region))
    error_message = "Region must be a valid AWS region identifier (e.g. eu-west-1)."
  }
}

# -----------------------------------------------------------------------------
# Notification Settings
# -----------------------------------------------------------------------------
variable "alert_email" {
  description = <<-EOT
    Email address that receives scanner reports via SNS.  The subscriber must
    confirm the subscription by clicking the link in the initial SNS email.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^@]+@[^@]+\\.[^@]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

# -----------------------------------------------------------------------------
# Scan Schedule
# -----------------------------------------------------------------------------
variable "scan_schedule" {
  description = <<-EOT
    EventBridge schedule expression that controls how often the scanner runs.
    Accepts both rate expressions and cron expressions:
      • rate(7 days)           — every 7 days
      • cron(0 8 ? * MON *)   — every Monday at 08:00 UTC
  EOT
  type        = string
  default     = "rate(7 days)"
}

# -----------------------------------------------------------------------------
# Idle Instance Detection
# -----------------------------------------------------------------------------
variable "idle_cpu_threshold" {
  description = <<-EOT
    Average CPU utilisation percentage below which an EC2 instance is considered
    idle.  The scanner checks the trailing 14-day average via CloudWatch.
    Instances below this threshold appear in the waste report.
  EOT
  type        = number
  default     = 5

  validation {
    condition     = var.idle_cpu_threshold > 0 && var.idle_cpu_threshold <= 100
    error_message = "idle_cpu_threshold must be between 1 and 100."
  }
}
