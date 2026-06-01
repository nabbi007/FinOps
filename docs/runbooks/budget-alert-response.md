# Runbook: Budget Alert Response

**Purpose:** Define the response when an AWS Budget alert fires for the Cost Detective account.

**Owner:** FinOps / Cloud Platform
**Region:** `eu-west-1`
**Trigger:** SNS email from the `cost-detective-budget-alerts` topic.

---

## What the Alerts Mean

The `budgets` module creates a monthly 50 USD cost budget with two notifications:

| Alert | Fires when | Severity | Meaning |
|-------|-----------|----------|---------|
| ACTUAL over 80% | Real month-to-date spend exceeds 40 USD | Warning | Approaching the ceiling; act now. |
| FORECASTED over 100% | AWS projects month-end spend will exceed 50 USD | Action | The trajectory will breach the budget unless something changes. |

Thresholds are percentages of the `budget_limit` variable. Change the limit in `terraform/terraform.tfvars` (or the `budgets` module) and re-apply.

---

## Response Procedure

### Step 1: Acknowledge and Confirm

- Open AWS Cost Explorer and confirm the spend trend.
- Distinguish a one-off (such as a deliberate load test) from a sustained increase.

### Step 2: Find the Driver

- Cost Explorer, grouped by Service: identify which service increased.
- Cost Explorer, grouped by Tag (CostCenter or Environment): identify the owning team or environment. This requires active cost-allocation tags; see the [tagging policy](../tagging-policy.md) and [tagging-noncompliance.md](tagging-noncompliance.md).

### Step 3: Hunt for Waste

```bash
python scripts/zombie_hunter.py --dry-run --region eu-west-1 --output report.json
```

Decommission confirmed waste using the [Zombie Cleanup runbook](zombie-cleanup.md).

### Step 4: Right-Size and Optimize

- Idle or oversized EC2: stop, downsize, or move stateless workloads onto the Spot ASG (`terraform/asg-spot/`).
- Unattached EBS or unassociated EIPs: decommission as in Step 3.
- Consider Savings Plans or Reserved capacity for steady-state usage.

### Step 5: Communicate and Document

- Notify the owning team via the `CostCenter` or `Owner` tag.
- Record the cause, the action taken, and the projected saving.
- If the higher spend is legitimate, raise the budget deliberately rather than ignoring the alert.

---

## Escalation

| Situation | Escalate to |
|-----------|-------------|
| Forecast alert with no obvious waste | FinOps lead or account owner; review the architecture for a sustained increase. |
| Unexpected spend in an unknown service | Security review; rule out compromised credentials or unauthorized usage. |
| Repeated monthly breaches | Re-baseline the budget and revisit cost ownership per team. |

---

## Prevention

- Tag enforcement (`terraform/config-rules/`) keeps spend attributable.
- Weekly automated scans (`terraform/lambda-scanner/`) catch waste before it accumulates.
- Spot and Auto Scaling (`terraform/asg-spot/`) keep compute cost-efficient.
- Review the budget and actuals in a monthly cost review.

---

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| No alert email ever arrives | SNS subscription not confirmed | Click the confirmation link AWS emailed after `terraform apply`. |
| Alert fired but spend looks low | Forecast alert (projection, not actual) | Expected behavior; it is a forward-looking warning. |
| Alerts needed in a chat channel rather than email | Only an email subscription exists | Add an SNS subscription (HTTPS, Lambda, or chat integration) to the budget topic. |
