# 🕵️ The Cost Detective — AWS FinOps Audit

> **Scenario:** You've inherited an AWS account from a previous team that was reckless with spending. Your budget is tight. You must identify waste, implement governance, and propose a savings plan.

---

## 🎯 Project Objectives

| # | Objective | Status |
|---|-----------|--------|
| 1 | Analyze existing spend to identify "Zombie Assets" | 🔲 |
| 2 | Implement active cost controls (Budgets and Alerts) | 🔲 |
| 3 | Architect a "Cost-Aware" solution using Spot Instances | 🔲 |
| 4 | Document everything for audit submission | 🔲 |

---

## 📁 Project Structure

```
cost-detective/
├── scripts/
│   ├── zombie_hunter.py          # Boto3 script to find & garbage-collect waste
│   └── tagging_compliance.py     # Check resources for missing CostCenter tags
├── terraform/
│   ├── budgets/                  # AWS Budgets + SNS alerts
│   ├── config-rules/             # AWS Config tagging enforcement rules
│   ├── asg-spot/                 # Auto Scaling Group with mixed instances policy
│   └── lambda-scanner/           # Lambda + EventBridge for weekly zombie scans
├── docs/
│   ├── cost-optimization-guide.md   # End-to-end practical guide
│   ├── tagging-policy.md            # Tagging standards & enforcement strategy
│   └── runbooks/
│       ├── zombie-cleanup.md
│       └── budget-alert-response.md
└── README.md
```

---

## 🗓️ Execution Plan

| Week | Phase | Tasks |
|------|-------|-------|
| **Week 1** | Analysis & Cleanup | Launch sandbox waste → detect with Boto3 + Trusted Advisor |
| **Week 2** | Governance | AWS Budgets + SNS + Tagging Policy + Config Rule |
| **Week 3** | Optimization | Auto Scaling Group with Spot Instances mix |
| **Week 4** | Automation | Lambda + EventBridge weekly scanner |
| **Week 4** | Documentation | Finalize all docs + live walkthrough recording |

---

## ⚙️ Prerequisites

- AWS Account (sandbox/free-tier)
- AWS CLI configured (`aws configure`)
- Python 3.x + Boto3 (`pip install boto3`)
- Terraform v1.x
- IAM permissions: EC2, EBS, EIP, Budgets, SNS, Config, Lambda, CloudWatch

---

## 🚀 Quick Start

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Configure AWS CLI
aws configure
# Set region to: eu-west-1

# 3. Run zombie hunter (dry run — no deletions)
python scripts/zombie_hunter.py --dry-run --region eu-west-1

# 4. Deploy governance infrastructure
cd terraform/budgets
terraform init && terraform apply

# 5. Deploy Auto Scaling Group with Spot Instances
cd terraform/asg-spot
terraform init && terraform apply
```

---

## 📚 Documentation

- [Cost Optimization Guide](docs/cost-optimization-guide.md)
- [Tagging Policy](docs/tagging-policy.md)
- [Zombie Cleanup Runbook](docs/runbooks/zombie-cleanup.md)
- [Budget Alert Response Runbook](docs/runbooks/budget-alert-response.md)

---

## 🎯 WALK Phase Maturity Targets

- ✅ Automated weekly scans for zombie resources (Lambda + EventBridge)
- ✅ Budgets per environment (dev, staging, prod) with team alerts
- ✅ AWS Config rules enforce tagging at resource creation
- ✅ Auto Scaling Groups with 20-30% Spot Instances
- ✅ CloudWatch dashboards showing cost trends by service
- ✅ Runbooks for common cost scenarios
