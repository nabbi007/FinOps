# =============================================================================
# Cost Detective — Root Variables
# =============================================================================
# Single source of truth for inputs shared across all modules. Module-specific
# settings (tag list, spot percentage, scan schedule, etc.) keep their own
# defaults inside each module and can still be overridden there if needed.
# =============================================================================

variable "region" {
  description = "AWS region for the entire Cost Detective stack."
  type        = string
  default     = "eu-west-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.region))
    error_message = "Region must be a valid AWS region identifier (e.g. eu-west-1)."
  }
}

variable "alert_email" {
  description = "Email for budget AND scanner SNS alerts. Confirm the subscription after apply."
  type        = string

  validation {
    condition     = can(regex("^[^@]+@[^@]+\\.[^@]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "budget_limit" {
  description = "Monthly budget limit in USD (alert thresholds are percentages of this)."
  type        = string
  default     = "50"
}
