# The Cost Detective — Project Walkthrough

A guided, end-to-end walkthrough of the AWS FinOps audit: identifying waste, proving it, putting governance and automation in place, and decommissioning safely. Every step is backed by evidence captured from a live run.

---

## 1. Scenario and Goal

An AWS account was inherited from a team that managed it without cost discipline. The mandate is to behave like a "cost detective": find the waste, prove the savings, and put controls in place so the account does not drift back. The work is organized around six objectives.

| # | Objective | Outcome |
|---|-----------|---------|
| 1 | Analyze spend and identify zombie assets | Detected and quantified (Section 4) |
| 2 | Active cost controls (budgets and alerts) | Deployed (Section 6) |
| 3 | Cost-aware architecture (Spot) | Deployed (Section 7) |
| 4 | Automation (scheduled scans) | Deployed and verified by email (Section 8) |
| 5 | Governance (tag enforcement) | Deployed (Section 6) |
| 6 | Safe decommissioning and documentation | Implemented (Section 9) and this document |

---

## 2. Executive Summary

- **Waste identified:** four idle/orphaned resources, totalling approximately **4.45 USD per month (53.40 USD per year)** in a small sandbox; the same patterns scale to thousands of dollars in a production account.
- **Investigation:** every detected resource was checked against CloudTrail and assigned a lifecycle tier, so deletion decisions are based on evidence, not just resource state.
- **Governance and automation deployed:** a monthly budget with alerts, AWS Config tag enforcement, an Auto Scaling Group blending On-Demand and Spot capacity, and a Lambda that scans for waste weekly and emails a report.
- **Safety:** the decommissioning tool only deletes resources proven idle for 30+ days, backs each one up first, and records a rollback path.

---

## 3. Environment

| Item | Value |
|------|-------|
| AWS account | `259401054581` (sandbox) |
| Region | `eu-west-1` |
| Tooling | Python 3.13 + boto3, Terraform 1.5+ |
| Repository | `scripts/` (detection and cleanup), `terraform/` (governance and automation), `docs/` (guides and runbooks) |

---

## 4. Phase 1 — Establish a Baseline of Waste

To have something concrete to detect, a small set of deliberately wasteful resources was provisioned with `create_sandbox_waste.py`, all tagged `Project=cost-detective-demo`.

```bash
python scripts/create_sandbox_waste.py --region eu-west-1
```

This created three resources, each representing a common real-world waste pattern.

**Unattached EBS volume** — 10 GiB of gp3 storage attached to nothing, yet billed every month:

![Unattached EBS volume in the EC2 console](pics/orphaned_volume.png)

*The `cost-detective-orphan-volume` (`vol-0a451a08d63a15ea4`) sits with no attachment.*

**Unassociated Elastic IP** — an allocated public IP not attached to any instance, charged at roughly 3.65 USD per month:

![Unassociated Elastic IP in the EC2 console](pics/unsed_eip.png)

*The `cost-detective-unused-eip` (`eipalloc-0f3208aa79013cef2`, `3.248.111.236`) has no associated instance.*

**Idle EC2 instance** — a running `t3.micro` doing no useful work:

![Idle EC2 instance in the EC2 console](pics/idle_instance.png)

*The `idle-demo` instance (`i-00adfae3d37c4bd9f`) is running with negligible utilization.*

---

## 5. Phase 2 — Detect and Investigate

`zombie_hunter.py` was run in read-only mode. It performs detection, then investigates each finding in CloudTrail before any decision is made.

```bash
python scripts/zombie_hunter.py --dry-run --region eu-west-1 --output report.json
```

![Zombie Hunter dry-run output](pics/zombie_hunter.png)

*A single read-only run: detection, CloudTrail investigation, the cost summary, and the lifecycle tiers.*

**How detection works.** The tool issues three filtered AWS queries: EBS volumes with status `available`, Elastic IPs with no association, and running EC2 instances whose CPU and network are both below threshold over a 14-day window. Importantly, this is account-wide — note that the scan found **four** resources, not just the three created above: it also picked up an Auto Scaling Group instance (`cost-detective-asg-instance`), and correctly **skipped** a second ASG instance that had real network traffic (6.06 MB/day) as "active, not idle." This demonstrates the network-aware idle check preventing a false positive.

**How investigation works.** Each detected resource is looked up in CloudTrail to find its last mutating activity, then placed in a lifecycle tier. Because these resources were just created, every one is correctly tiered `ACTIVE` ("provisioned, never used since"), and the report shows the principal who created each. The result: nothing is eligible for deletion, which is the safety gate working as designed.

**The tiers are configurable.** Tightening the thresholds to 1/2/3 days reclassifies the same two-day-old resources from `ACTIVE` into `ESCALATE`, illustrating how the lifecycle policy adapts:

![Zombie Hunter tier threshold demonstration](pics/tier_demo.png)

*With `--flag-days 1 --escalate-days 2 --decommission-days 3`, the resources move into the ESCALATE tier.*

The full lifecycle model:

| Tier | Condition | Action |
|------|-----------|--------|
| `EXEMPT` | Tagged `DoNotDelete` | Never deleted |
| `ACTIVE` | Idle < 7 days | Protected |
| `FLAG` | Idle 7–13 days | Review / notify owner |
| `ESCALATE` | Idle 14–29 days | Escalate; not deleted |
| `SAFE` | Idle ≥ 30 days | The only deletable tier |
| `INCONCLUSIVE` | No activity found | Protected; investigate |
| `UNVERIFIABLE` | CloudTrail error | Protected (fail-safe) |

---

## 6. Phase 3 — Governance and Cost Controls

With waste quantified, governance was deployed via Terraform. The umbrella root applies all modules together:

```bash
cd terraform
terraform init
terraform apply        # alert_email is read from terraform.tfvars
```

**Budgets and alerts (`budgets` module).** A monthly 50 USD cost budget publishes to an SNS topic and email when actual spend exceeds 80% or forecast spend exceeds 100%. Spend itself is tracked in Cost Explorer:

![Cost Explorer cost and usage graph](pics/cost_explorer.png)

*Cost Explorer cost-and-usage view used to confirm spend trends behind a budget alert.*

**Tag enforcement (`config-rules` module).** AWS Config records resource configurations and continuously evaluates a `required-tags` rule, flagging any EC2 instance missing `CostCenter` or `Environment`. This keeps all spend attributable for chargeback and per-environment analysis. The `tagging_compliance.py` script provides the same check on demand.

---

## 7. Phase 4 — Cost-Aware Architecture

The `asg-spot` module provisions an Auto Scaling Group with a mixed-instances policy: a guaranteed On-Demand base plus Spot capacity for the remainder, using the `capacity-optimized` allocation strategy across several instance types. Spot capacity is 60–90% cheaper than On-Demand, making this the right pattern for stateless, fault-tolerant workloads. Interruption handling is documented in the [Spot Interruption runbook](docs/runbooks/spot-interruption.md).

---

## 8. Phase 5 — Automation

The `lambda-scanner` module runs the same detection logic on a schedule. EventBridge triggers the Lambda every seven days; it scans for waste and publishes a report to SNS for email delivery. The automated report was received during the run:

![Weekly waste report email from the scanner](pics/scanner_mail.png)

*The scheduled scanner's SNS email: a weekly waste report listing the unattached volume, unassociated Elastic IP, and idle instance.*

Receiving this email confirms the entire automation chain end to end — EventBridge invocation, the Lambda's IAM permissions, the scan logic, and SNS delivery — and means the SNS subscription was confirmed.

---

## 9. Phase 6 — Safe Decommissioning

Detection finds waste; decommissioning removes it without risk. `zombie_hunter.py --execute` applies several safeguards, and only ever acts on the `SAFE` tier.

```bash
python scripts/zombie_hunter.py --execute --region eu-west-1
```

In this sandbox the resources were freshly created and therefore `ACTIVE`, so the tool correctly refused to delete anything — the gate proving its value. When a resource is genuinely `SAFE` (idle 30+ days), the process is:

1. **Re-verify** the resource is still wasteful (guards against a state change since the scan).
2. **Back up and verify:** snapshot an EBS volume (or image an instance before termination) and wait for completion. If the backup fails, the deletion is aborted.
3. **Act:** delete the volume, release the Elastic IP, or stop the instance (stopping is reversible and the default).
4. **Record:** every action, backup ID, and an exact rollback command is written to a decommission manifest (`decommission-log-<timestamp>.json`).

Operational detail is in the [Zombie Cleanup runbook](docs/runbooks/zombie-cleanup.md).

---

## 10. Results Against Objectives

| Objective | Evidence in this document |
|-----------|---------------------------|
| Identify zombie assets | Section 4 (console) and Section 5 (detection output) |
| Active cost controls | Section 6 (budget, SNS, Cost Explorer) |
| Cost-aware architecture | Section 7 (Spot ASG) |
| Automation | Section 8 (scheduled scanner email) |
| Governance | Section 6 (AWS Config tag rule) |
| Safe decommissioning and documentation | Section 9 and this walkthrough |

**Quantified result:** 4.45 USD/month (53.40 USD/year) of waste identified in a sandbox, with a repeatable, automated process to keep finding and removing it.

---

## 11. Engineering and Safety Highlights

- **Evidence-based deletion:** a resource is removed only after CloudTrail proves it idle for 30+ days.
- **Network-aware idle detection:** both CPU and network must be low, preventing low-CPU-but-busy services from being misclassified (demonstrated in Section 5).
- **Backup before delete:** verified snapshot/AMI first; the delete is aborted if the backup fails.
- **Fail-safe by default:** credential, permission, or CloudTrail errors protect resources rather than deleting them.
- **Exemption tag:** `DoNotDelete` overrides everything.
- **Full audit trail:** JSON reports for detection and a manifest with rollback commands for every decommissioning action.

---

## 12. Reproduce It

```bash
pip install -r requirements.txt

# Baseline, detect, govern
python scripts/create_sandbox_waste.py --region eu-west-1
python scripts/zombie_hunter.py --dry-run --region eu-west-1 --output report.json
cd terraform && terraform init && terraform apply

# Teardown
terraform destroy
python scripts/create_sandbox_waste.py --region eu-west-1 --cleanup
```

---

## 13. Further Documentation

- [README](README.md) — full toolkit reference
- [Cost Optimization Guide](docs/cost-optimization-guide.md)
- [Tagging Policy](docs/tagging-policy.md)
- Runbooks: [Zombie Cleanup](docs/runbooks/zombie-cleanup.md), [Budget Alert Response](docs/runbooks/budget-alert-response.md), [Tagging Non-Compliance](docs/runbooks/tagging-noncompliance.md), [Spot Interruption](docs/runbooks/spot-interruption.md), [Weekly Scan Review](docs/runbooks/weekly-scan-review.md)
