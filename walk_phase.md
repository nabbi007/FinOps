WALK Phase (Proactive - Building Capability) ⭐ Your Target
Characteristics: Some automation, cross-team awareness, proactive optimization

For This Project:

1. Analysis & Cleanup

Automated weekly scans for zombie resources (Lambda + EventBridge)

Script identifies: unattached EBS, unassociated EIPs, idle EC2s

Sends reports to team Slack/Email before cleanup

Requires manual approval to delete (safety net)

Track savings from cleanup activities

2. Governance

Budgets per environment (dev, staging, prod) with team alerts

AWS Config rules enforce tagging at resource creation

Monthly cost review meetings with stakeholders

Cost allocation tags active for basic chargeback

Tagging policy documented and shared

3. Optimization Architecture

Auto Scaling Groups with 20-30% Spot Instances

Basic right-sizing recommendations reviewed quarterly

Use Compute Savings Plans for predictable workloads

CloudWatch dashboards showing cost trends by service

Document cost-optimization patterns for teams

4. Culture & Process

Establish cost ownership per team/project

Create runbooks for common cost scenarios

Monthly cost optimization reviews

Basic cost forecasting for planning