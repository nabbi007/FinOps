#!/usr/bin/env python3
"""
zombie_hunter.py — AWS Zombie Resource Detector & Garbage Collector

Detects and optionally deletes zombie AWS resources:
  - Unattached EBS volumes
  - Unassociated Elastic IP addresses
  - Idle EC2 instances (< 5% avg CPU over 14 days)

Usage:
  python zombie_hunter.py --dry-run
  python zombie_hunter.py --execute
  python zombie_hunter.py --region eu-west-1 --output report.json
"""

import boto3
import argparse
import json
import datetime
from botocore.exceptions import ClientError


DEFAULT_REGION = "eu-west-1"
IDLE_CPU_THRESHOLD = 5.0
IDLE_LOOKBACK_DAYS = 14


def get_clients(region):
    """Initialize AWS service clients."""
    return {
        "ec2": boto3.client("ec2", region_name=region),
        "cloudwatch": boto3.client("cloudwatch", region_name=region),
    }


def find_unattached_ebs(ec2_client):
    """Find EBS volumes in 'available' state."""
    print("\nScanning for unattached EBS volumes...")

    response = ec2_client.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}]
    )

    zombies = []
    for volume in response["Volumes"]:
        size_gb = volume["Size"]
        vol_id = volume["VolumeId"]
        vol_type = volume["VolumeType"]
        created = volume["CreateTime"].strftime("%Y-%m-%d")
        tags = {t["Key"]: t["Value"] for t in volume.get("Tags", [])}

        cost_per_gb = 0.08 if vol_type == "gp3" else 0.10
        monthly_cost = size_gb * cost_per_gb

        zombies.append({
            "resource_type": "EBS Volume",
            "resource_id": vol_id,
            "details": f"{size_gb} GB {vol_type}",
            "created": created,
            "monthly_cost_usd": round(monthly_cost, 2),
            "tags": tags,
        })
        print(f"  WARNING: Volume {vol_id} | {size_gb}GB {vol_type} | Created: {created} | ~${monthly_cost:.2f}/mo")

    if not zombies:
        print("  OK: No unattached EBS volumes found.")
    return zombies


def delete_ebs_volumes(ec2_client, volumes):
    """Delete unattached EBS volumes."""
    for v in volumes:
        vol_id = v["resource_id"]
        try:
            ec2_client.delete_volume(VolumeId=vol_id)
            print(f"  DELETED: EBS volume: {vol_id}")
        except ClientError as e:
            print(f"  ERROR: Failed to delete {vol_id}: {e}")


def find_unassociated_eips(ec2_client):
    """Find Elastic IPs not associated with any instance."""
    print("\nScanning for unassociated Elastic IPs...")

    response = ec2_client.describe_addresses()
    zombies = []

    for addr in response["Addresses"]:
        if "AssociationId" not in addr:
            eip = addr.get("PublicIp", "N/A")
            alloc_id = addr.get("AllocationId", "N/A")
            tags = {t["Key"]: t["Value"] for t in addr.get("Tags", [])}

            zombies.append({
                "resource_type": "Elastic IP",
                "resource_id": alloc_id,
                "details": f"Public IP: {eip}",
                "created": "N/A",
                "monthly_cost_usd": 3.65,
                "tags": tags,
            })
            print(f"  WARNING: EIP {eip} | Allocation ID: {alloc_id} | ~$3.65/mo")

    if not zombies:
        print("  OK: No unassociated Elastic IPs found.")
    return zombies


def release_eips(ec2_client, eips):
    """Release unassociated Elastic IPs."""
    for eip in eips:
        alloc_id = eip["resource_id"]
        try:
            ec2_client.release_address(AllocationId=alloc_id)
            print(f"  RELEASED: Elastic IP: {alloc_id}")
        except ClientError as e:
            print(f"  ERROR: Failed to release {alloc_id}: {e}")


def get_average_cpu(cw_client, instance_id, days=IDLE_LOOKBACK_DAYS):
    """Get average CPU utilization over the past N days.

    Returns None when there are no datapoints (e.g. a just-launched instance
    or one with detailed monitoring disabled) or when the API call fails.
    Callers must treat None as "unknown" — NOT as idle — so we never stop an
    instance we have no utilization data for.
    """
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(days=days)

    try:
        response = cw_client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Average"],
        )
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        avg = sum(d["Average"] for d in datapoints) / len(datapoints)
        return round(avg, 2)
    except ClientError:
        return None


def find_idle_ec2_instances(ec2_client, cw_client):
    """Find running EC2 instances with very low CPU utilization."""
    print(f"\nScanning for idle EC2 instances (avg CPU < {IDLE_CPU_THRESHOLD}% over {IDLE_LOOKBACK_DAYS} days)...")

    response = ec2_client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )

    zombies = []
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_id = instance["InstanceId"]
            instance_type = instance["InstanceType"]
            launch_time = instance["LaunchTime"].strftime("%Y-%m-%d")
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            name = tags.get("Name", "Unnamed")

            avg_cpu = get_average_cpu(cw_client, instance_id)

            if avg_cpu < IDLE_CPU_THRESHOLD:
                zombies.append({
                    "resource_type": "EC2 Instance",
                    "resource_id": instance_id,
                    "details": f"{instance_type} | Name: {name} | Avg CPU: {avg_cpu}%",
                    "created": launch_time,
                    "monthly_cost_usd": "varies",
                    "tags": tags,
                })
                print(f"  WARNING: Instance {instance_id} ({instance_type}) | Name: {name} | Avg CPU: {avg_cpu}%")

    if not zombies:
        print("  OK: No idle EC2 instances found.")
    return zombies


def stop_idle_instances(ec2_client, instances):
    """Stop idle EC2 instances."""
    for inst in instances:
        inst_id = inst["resource_id"]
        try:
            ec2_client.stop_instances(InstanceIds=[inst_id])
            print(f"  STOPPED: EC2 instance: {inst_id}")
        except ClientError as e:
            print(f"  ERROR: Failed to stop {inst_id}: {e}")


def generate_report(all_zombies, region):
    """Generate summary report of zombie resources."""
    total_monthly_cost = 0
    for z in all_zombies:
        try:
            total_monthly_cost += float(z["monthly_cost_usd"])
        except (ValueError, TypeError):
            pass

    report = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "region": region,
        "total_zombies_found": len(all_zombies),
        "estimated_monthly_waste_usd": round(total_monthly_cost, 2),
        "estimated_annual_waste_usd": round(total_monthly_cost * 12, 2),
        "zombie_resources": all_zombies,
    }
    return report


def print_summary(report):
    """Print a human-readable summary."""
    print("\n" + "=" * 60)
    print("ZOMBIE HUNTER REPORT")
    print("=" * 60)
    print(f"  Generated:        {report['generated_at']}")
    print(f"  Region:           {report['region']}")
    print(f"  Zombies Found:    {report['total_zombies_found']}")
    print(f"  Monthly Waste:    ${report['estimated_monthly_waste_usd']:.2f}")
    print(f"  Annual Waste:     ${report['estimated_annual_waste_usd']:.2f}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Zombie Hunter - Find and clean up wasteful AWS resources"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect zombies only. Do NOT delete/stop anything.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete/stop detected zombie resources. USE WITH CAUTION.",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region to scan (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Save report to a JSON file (e.g., report.json)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("⚠️  Please specify --dry-run or --execute")
        parser.print_help()
        return

    print("=" * 60)
    print("THE COST DETECTIVE - Zombie Hunter")
    print(f"   Region: {args.region}")
    print(f"   Mode:   {'DRY RUN (no deletions)' if args.dry_run else 'WARNING: EXECUTE (will delete/stop resources)'}")
    print("=" * 60)

    clients = get_clients(args.region)
    ec2 = clients["ec2"]
    cw = clients["cloudwatch"]

    # Detect all zombie resources
    ebs_zombies = find_unattached_ebs(ec2)
    eip_zombies = find_unassociated_eips(ec2)
    ec2_zombies = find_idle_ec2_instances(ec2, cw)

    all_zombies = ebs_zombies + eip_zombies + ec2_zombies

    # Generate and print report
    report = generate_report(all_zombies, args.region)
    print_summary(report)

    # Save report if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to: {args.output}")

    # Execute cleanup if requested
    if args.execute and all_zombies:
        print("\nWARNING: EXECUTING CLEANUP...")
        confirm = input("Type 'yes' to confirm deletion of all detected zombie resources: ")
        if confirm.lower() == "yes":
            if ebs_zombies:
                delete_ebs_volumes(ec2, ebs_zombies)
            if eip_zombies:
                release_eips(ec2, eip_zombies)
            if ec2_zombies:
                stop_idle_instances(ec2, ec2_zombies)
            print("\nCleanup complete!")
        else:
            print("Cleanup cancelled.")
    elif args.dry_run:
        print("\nDry run complete. No resources were modified.")


if __name__ == "__main__":
    main()
