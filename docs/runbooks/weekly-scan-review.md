# Runbook: Weekly Scan Review

**Purpose:** Process the automated weekly waste report produced by the Lambda scanner, so that newly accumulated waste is reviewed and acted on promptly.

**Owner:** FinOps / Cloud Platform
**Region:** `eu-west-1`
**Component:** `terraform/lambda-scanner/`
**Trigger:** SNS email from the `cost-detective-scanner-alerts` topic (sent on the EventBridge schedule, by default `rate(7 days)`).

---

## Background

The `lambda-scanner` module runs the same detection logic as `zombie_hunter.py` on a schedule. EventBridge invokes the Lambda, which scans for unattached EBS volumes, unassociated Elastic IPs, and idle EC2 instances, then publishes a summary to SNS for email delivery.

![Example weekly waste report email](../../pics/scanner_mail.png)

The scheduled report is a detection-only signal. It does not delete anything; remediation is performed deliberately using the Zombie Cleanup runbook.

---

## When to Use

- A weekly scanner report arrives by email.
- After deploying or changing the `lambda-scanner` module, to validate it.

---

## Step 1: Triage the Report

Read the report summary: total findings, and the counts of unattached volumes, unassociated EIPs, and idle instances.

- Zero findings: no action required; file the report as evidence of a clean account.
- New findings since last week: proceed to Step 2.

---

## Step 2: Investigate and Confirm

The email report lists resources by state only; it does not include CloudTrail tiers. Run the full hunter to add the lifecycle investigation before acting:

```bash
python scripts/zombie_hunter.py --dry-run --region eu-west-1 --output report.json
```

Cross-reference the resources in the email with the tiered output. Investigate any uncertain item with `cloudtrail_tracker.py`.

---

## Step 3: Remediate

Follow the [Zombie Cleanup runbook](zombie-cleanup.md) to decommission confirmed SAFE-tier waste. Tag any intentionally retained resource with `DoNotDelete` so it is excluded from future scans' remediation.

---

## Step 4: Trigger an Out-of-Cycle Scan (Optional)

To produce a report on demand rather than waiting for the schedule:

```bash
aws lambda invoke --function-name cost-detective-scanner --region eu-west-1 out.json
cat out.json
```

The JSON response is returned locally and the email report is delivered through SNS.

---

## Step 5: Adjust the Cadence (Optional)

The schedule is controlled by the `scan_schedule` variable on the `lambda-scanner` module. Examples:

```hcl
scan_schedule = "rate(7 days)"          # weekly (default)
scan_schedule = "rate(1 day)"           # daily
scan_schedule = "cron(0 8 ? * MON *)"   # every Monday at 08:00 UTC
```

Apply the change through Terraform.

---

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| No weekly email ever arrives | SNS subscription not confirmed | Click the confirmation link sent after `terraform apply`. |
| Email received but lists no resources | Account is clean, or the scanner lacks read permissions | Confirm with a manual `zombie_hunter.py --dry-run`; check the Lambda's IAM role and CloudWatch logs. |
| Lambda errors in CloudWatch logs | Missing permissions or a runtime error | Review `/aws/lambda/cost-detective-scanner` logs; verify the execution role policy. |
| Report cadence is wrong | `scan_schedule` value | Update the variable and re-apply (Step 5). |

---

## Related

- Manual detection and cleanup: [Zombie Cleanup](zombie-cleanup.md)
- Module: `terraform/lambda-scanner/`
