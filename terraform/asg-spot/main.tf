# =============================================================================
# Cost Detective — ASG with Spot Instances Module
# =============================================================================
# This module provisions an Auto Scaling Group that uses a mixed-instances
# policy to blend On-Demand and Spot capacity.  The "capacity-optimized"
# allocation strategy directs Spot requests to the pools with the most
# available capacity, reducing the likelihood of interruptions.
#
# Architecture overview:
#   Launch Template  ──►  Auto Scaling Group (mixed-instances policy)
#       │                        │
#       └── Amazon Linux 2023    ├── On-Demand base (configurable)
#           + nginx user_data    └── Spot remainder  (capacity-optimized)
#
# Cost optimisation levers:
#   • on_demand_base_capacity            – guaranteed minimum On-Demand count
#   • on_demand_percentage_above_base    – ratio of OD vs Spot above the base
#   • launch_template overrides          – multiple instance types widen Spot pools
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform & Provider Configuration
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Default tags applied to every resource created by this module.
  default_tags {
    tags = {
      Project   = "CostDetective"
      ManagedBy = "Terraform"
      Module    = "asg-spot"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources — Default VPC & Subnets
# -----------------------------------------------------------------------------
# We use the default VPC for simplicity.  In production you would reference a
# purpose-built VPC via a remote-state data source or variable.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  # Only select subnets that auto-assign public IPs (default-VPC behaviour).
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# -----------------------------------------------------------------------------
# Data Source — Latest Amazon Linux 2023 AMI
# -----------------------------------------------------------------------------
# Automatically resolves the most recent AL2023 AMI owned by Amazon so the
# launch template always boots a patched image without manual AMI lookups.

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# -----------------------------------------------------------------------------
# Launch Template
# -----------------------------------------------------------------------------
# Defines the instance blueprint: AMI, instance type, user-data script, and
# cost-tracking tags.  The ASG references this template but may override the
# instance type through its mixed-instances policy.

resource "aws_launch_template" "app" {
  name_prefix   = "cost-detective-"
  description   = "Launch template for Cost Detective ASG – Amazon Linux 2023 with nginx"
  image_id      = data.aws_ami.amazon_linux_2023.id
  instance_type = var.instance_type

  # ---------------------------------------------------------------------------
  # User Data — Bootstrap nginx on first boot
  # ---------------------------------------------------------------------------
  # The script installs nginx via dnf (AL2023 package manager), enables the
  # service, and writes a simple health-check landing page.
  user_data = base64encode(<<-USERDATA
    #!/bin/bash
    set -euo pipefail

    # ---- Update packages & install nginx ----
    dnf update -y
    dnf install -y nginx

    # ---- Write a minimal health-check page ----
    cat > /usr/share/nginx/html/index.html <<'HTML'
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><title>Cost Detective</title></head>
    <body>
      <h1>Cost Detective — Healthy ✅</h1>
      <p>Instance launched by ASG with Spot optimisation.</p>
    </body>
    </html>
    HTML

    # ---- Enable & start nginx ----
    systemctl enable nginx
    systemctl start nginx
  USERDATA
  )

  # ---------------------------------------------------------------------------
  # Tags applied to instances and volumes created from this template
  # ---------------------------------------------------------------------------
  tag_specifications {
    resource_type = "instance"
    tags = merge(var.tags, {
      Name       = "cost-detective-asg-instance"
      LaunchFrom = "asg-spot-module"
    })
  }

  tag_specifications {
    resource_type = "volume"
    tags = merge(var.tags, {
      Name = "cost-detective-asg-volume"
    })
  }

  # Template-level tags (visible in the EC2 Launch Templates console).
  tags = merge(var.tags, {
    Name = "cost-detective-launch-template"
  })

  # Ensure a new template version is created before the old one is destroyed.
  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# Auto Scaling Group — Mixed Instances Policy
# -----------------------------------------------------------------------------
# The mixed-instances policy allows the ASG to launch a blend of On-Demand and
# Spot instances across multiple instance types.  This:
#   1. Reduces cost by leveraging Spot pricing (~60-90 % savings).
#   2. Improves availability by diversifying across instance pools.
#   3. Guarantees a minimum On-Demand base for baseline capacity.

resource "aws_autoscaling_group" "app" {
  name                = "cost-detective-asg"
  vpc_zone_identifier = data.aws_subnets.default.ids

  # Capacity settings — controlled via variables for flexibility.
  min_size         = var.min_size
  max_size         = var.max_size
  desired_capacity = var.desired_capacity

  # EC2-level health checks (use ELB if fronted by an ALB/NLB).
  health_check_type         = "EC2"
  health_check_grace_period = 300 # seconds

  # ---------------------------------------------------------------------------
  # Mixed Instances Policy
  # ---------------------------------------------------------------------------
  mixed_instances_policy {

    # --- Launch Template ---
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.app.id
        version            = "$Latest"
      }

      # Override the default instance type with several alternatives.
      # Wider type diversity = more Spot capacity pools = fewer interruptions.
      override {
        instance_type = "t3.micro"
      }
      override {
        instance_type = "t3a.micro"
      }
      override {
        instance_type = "t2.micro"
      }
    }

    # --- Instances Distribution ---
    instances_distribution {
      # At least 1 instance is always On-Demand (the "base").
      on_demand_base_capacity = 1

      # Of capacity ABOVE the base, 70 % On-Demand / 30 % Spot.
      on_demand_percentage_above_base_capacity = 100 - var.spot_percentage

      # "capacity-optimized" picks the Spot pool with the most spare capacity,
      # which statistically has the lowest interruption rate.
      spot_allocation_strategy = "capacity-optimized"
    }
  }

  # ---------------------------------------------------------------------------
  # ASG-level Tags — propagated to every launched instance
  # ---------------------------------------------------------------------------
  # Note: dynamic block used so that arbitrary tags from var.tags are included.
  dynamic "tag" {
    for_each = merge(var.tags, {
      CostCenter  = lookup(var.tags, "CostCenter", "FinOps")
      Environment = lookup(var.tags, "Environment", "development")
      Project     = lookup(var.tags, "Project", "CostDetective")
    })
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  # Prevent Terraform from fighting with external scaling actions.
  lifecycle {
    ignore_changes = [desired_capacity]
  }
}
