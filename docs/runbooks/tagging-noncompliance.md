# Runbook: Tagging Non-Compliance Remediation

**Purpose:** Detect and remediate resources that are missing required cost-allocation tags, so that all spend remains attributable for chargeback, showback, and per-environment analysis.

**Owner:** FinOps / Cloud Platform
**Region:** `eu-west-1`
**Required tags:** `CostCenter`, `Environment`, `Owner`, `Project`

---

## Why It Matters

Untagged resources are invisible in cost-allocation reports. Without `CostCenter`, chargeback and showback are impossible; without `Environment`, per-environment cost analysis cannot be performed. Tag enforcement is the foundation of every other FinOps activity in this project.

---

## When to Use

- AWS Config reports the `cost-detective-required-tags` rule as non-compliant.
- A budget investigation cannot attribute spend to a team because tags are missing.
- During onboarding of a new workload or team.

---

## Step 1: Scan for Non-Compliant Resources

```bash
python scripts/tagging_compliance.py --region eu-west-1 --output tagging-report.json
```

This scans EC2 instances, EBS volumes, and Elastic IPs and reports which are missing any required tag, with a compliance percentage. The exit code is non-zero if any resource is non-compliant, which makes the script suitable for CI gates.

Cross-check with AWS Config in the console: open the `cost-detective-required-tags` rule and review the non-compliant resources it lists.

---

## Step 2: Identify the Owner

For each non-compliant resource, determine the responsible team:

- Use any partial tags already present.
- Use CloudTrail to find who created the resource:

```bash
python scripts/cloudtrail_tracker.py --resource-id <id> --region eu-west-1
```

---

## Step 3: Apply the Missing Tags

Apply the required tags to each resource. Example for an EC2 instance:

```bash
aws ec2 create-tags --resources <id> --region eu-west-1 \
  --tags Key=CostCenter,Value=<center> \
         Key=Environment,Value=<dev|staging|prod> \
         Key=Owner,Value=<team-or-email> \
         Key=Project,Value=<project>
```

The same command works for EBS volumes and Elastic IP allocation IDs.

---

## Step 4: Verify

```bash
python scripts/tagging_compliance.py --region eu-west-1
```

The compliance rate should reach 100%. In AWS Config, the rule re-evaluates on the next configuration change; you can also trigger a re-evaluation manually from the console.

---

## Step 5: Prevent Recurrence

- The `config-rules` module flags non-compliant resources continuously, providing near-real-time detection.
- Enforce tagging at creation time wherever possible: require tags in Terraform and CloudFormation templates, and consider Service Control Policies or IAM tag-condition policies to deny resource creation without the mandatory tags.
- Document the standard in the [tagging policy](../tagging-policy.md) and review compliance in the monthly cost review.

---

## Escalation

| Situation | Escalate to |
|-----------|-------------|
| Owner cannot be determined from tags or CloudTrail | FinOps lead; treat as an orphaned resource and assess for decommissioning. |
| A team repeatedly creates untagged resources | Platform governance; introduce a preventive policy (SCP or IAM condition). |
| Required tag set needs to change | FinOps lead; update the `required_tags` variable and the tagging policy. |

---

## Related

- [Tagging Policy](../tagging-policy.md)
- [Budget Alert Response](budget-alert-response.md) (tags are needed to attribute spend)
- Enforcement module: `terraform/config-rules/`
