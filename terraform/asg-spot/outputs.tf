# =============================================================================
# Cost Detective — ASG Spot Module · Outputs
# =============================================================================
# Values exported for use by other modules, CI/CD pipelines, or the root
# Terraform configuration.
# =============================================================================

# -----------------------------------------------------------------------------
# Auto Scaling Group
# -----------------------------------------------------------------------------
output "asg_name" {
  description = "Name of the Auto Scaling Group."
  value       = aws_autoscaling_group.app.name
}

output "asg_arn" {
  description = "ARN of the Auto Scaling Group."
  value       = aws_autoscaling_group.app.arn
}

# -----------------------------------------------------------------------------
# Launch Template
# -----------------------------------------------------------------------------
output "launch_template_id" {
  description = "ID of the EC2 Launch Template used by the ASG."
  value       = aws_launch_template.app.id
}

# -----------------------------------------------------------------------------
# Informational — Resolved AMI
# -----------------------------------------------------------------------------
output "ami_id" {
  description = "AMI ID that was resolved for Amazon Linux 2023 (informational)."
  value       = data.aws_ami.amazon_linux_2023.id
}

output "ami_name" {
  description = "Human-readable name of the resolved AMI (informational)."
  value       = data.aws_ami.amazon_linux_2023.name
}
