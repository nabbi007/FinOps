# Runbook: Spot Interruption Handling

**Purpose:** Understand, monitor, and respond to EC2 Spot interruptions in the cost-optimized Auto Scaling Group, so that cost savings are retained without compromising availability.

**Owner:** FinOps / Cloud Platform
**Region:** `eu-west-1`
**Component:** `terraform/asg-spot/`

---

## Background

The `asg-spot` module runs an Auto Scaling Group with a mixed-instances policy: a guaranteed On-Demand base plus Spot capacity for the remainder. Spot instances are 60 to 90 percent cheaper than On-Demand but can be reclaimed by AWS with a two-minute warning when capacity is needed elsewhere.

This trade-off is appropriate for stateless, fault-tolerant workloads. The module reduces interruption impact by using the `capacity-optimized` allocation strategy and diversifying across multiple instance types, which directs Spot requests to the pools with the most spare capacity.

---

## When to Use

- A Spot interruption notice or rebalance recommendation has been observed.
- The ASG is repeatedly losing Spot capacity, affecting available instances.
- Reviewing whether the On-Demand and Spot balance is still appropriate.

---

## How an Interruption Plays Out

1. AWS issues a Spot interruption notice (a two-minute warning) and/or an EC2 rebalance recommendation.
2. The instance is reclaimed.
3. The Auto Scaling Group detects the reduced capacity and launches a replacement according to the mixed-instances policy (another Spot instance, or On-Demand if Spot is unavailable).

For a stateless workload behind the ASG, no manual action is normally required; the group self-heals.

---

## Step 1: Confirm What Happened

- EC2 console, Auto Scaling Groups: open `cost-detective-asg` and review the Activity history for launch and termination events.
- EC2 console, Instances: check the lifecycle of current instances (Spot versus On-Demand) and how many are running.
- CloudWatch and the EC2 events history: look for Spot interruption notices.

---

## Step 2: Assess Impact

- Did desired capacity recover automatically? If the group is back to its desired count, no action is needed.
- Was there a brief capacity dip? Acceptable for a stateless, horizontally scaled workload.
- Are replacements failing to launch? This indicates a Spot capacity shortage across the configured pools (see Step 3).

---

## Step 3: Respond to Persistent Interruptions

If Spot capacity is repeatedly unavailable:

1. Increase instance-type diversity. Add more comparable instance types to the launch-template overrides in `terraform/asg-spot/main.tf`; wider diversity means more Spot pools and fewer interruptions.
2. Raise the On-Demand base or the On-Demand percentage above the base by adjusting `spot_percentage` (and the base capacity), trading some savings for stability.
3. Confirm the allocation strategy remains `capacity-optimized`.

Apply changes through Terraform:

```bash
cd terraform/asg-spot
terraform plan
terraform apply
```

(Or from the umbrella root, after exposing the relevant variable.)

---

## Step 4: Protect Stateful Workloads

Do not place workloads that hold local state on Spot capacity. If state is unavoidable, ensure it is externalized (for example to a database, EFS, or S3) and that instances drain gracefully on the two-minute interruption notice using lifecycle hooks.

---

## Escalation

| Situation | Escalate to |
|-----------|-------------|
| Sustained inability to acquire Spot capacity in the region | Cloud Platform; consider a different region or a higher On-Demand ratio. |
| A stateful workload is suffering data loss on interruption | Application owner; the workload is mis-placed on Spot. |
| Availability requirements have changed | FinOps lead; rebalance the cost-versus-stability trade-off. |

---

## Related

- Module: `terraform/asg-spot/`
- [Cost Optimization Guide](../cost-optimization-guide.md)
