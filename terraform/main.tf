# =============================================================================
# Cost Detective — Root (Umbrella) Configuration
# =============================================================================
# Wires the four standalone modules together so the ENTIRE FinOps stack can be
# planned and applied from this directory in one command. alert_email is read
# automatically from terraform.tfvars, so no -var is needed:
#
#   terraform init
#   terraform plan
#   terraform apply
#
# Each module still self-configures its own AWS provider (region is passed in),
# so you can ALSO deploy any single module on its own, e.g.:
#   cd budgets && terraform init && terraform apply
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

# --- Governance: monthly budget + SNS email alerts ---
module "budgets" {
  source       = "./budgets"
  region       = var.region
  alert_email  = var.alert_email
  budget_limit = var.budget_limit
}

# --- Governance: AWS Config tagging-compliance enforcement ---
module "config_rules" {
  source = "./config-rules"
  region = var.region
}

# --- Optimization: Auto Scaling Group with mixed On-Demand/Spot capacity ---
module "asg_spot" {
  source = "./asg-spot"
  region = var.region
}

# --- Automation: weekly Lambda waste scanner (EventBridge + SNS) ---
module "lambda_scanner" {
  source      = "./lambda-scanner"
  region      = var.region
  alert_email = var.alert_email
}
