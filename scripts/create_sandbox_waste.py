#!/usr/bin/env python3
"""
create_sandbox_waste.py — Create Demo Wasteful AWS Resources

Provisions wasteful AWS resources for testing:
  1. Unattached EBS volume (10 GB, gp3)
  2. Unassociated Elastic IP
  3. Idle EC2 instance (t3.micro)

All resources tagged with Project=cost-detective-demo for easy cleanup.

Usage:
  python create_sandbox_waste.py --region eu-west-1
  python create_sandbox_waste.py --region eu-west-1 --cleanup

Exit codes:
  0 – success
  1 – partial failure
  2 – fatal error
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


DEMO_TAG_KEY = "Project"
DEMO_TAG_VALUE = "cost-detective-demo"

COMMON_TAGS: list[dict[str, str]] = [
    {"Key": DEMO_TAG_KEY, "Value": DEMO_TAG_VALUE},
    {"Key": "CostCenter", "Value": "finops-sandbox"},
    {"Key": "Environment", "Value": "sandbox"},
    {"Key": "Owner", "Value": "cost-detective"},
    {"Key": "ManagedBy", "Value": "create_sandbox_waste.py"},
]

EBS_SIZE_GB = 10
EBS_VOLUME_TYPE = "gp3"
EC2_INSTANCE_TYPE = "t3.micro"


_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def green(t: str) -> str:
    return _c("32", t)


def red(t: str) -> str:
    return _c("31", t)


def yellow(t: str) -> str:
    return _c("33", t)


def bold(t: str) -> str:
    return _c("1", t)


def _get_default_vpc_subnet(ec2_client: Any) -> str | None:
    """Return first subnet in default VPC or any available subnet."""
    try:
        vpcs = ec2_client.describe_vpcs(
            Filters=[{"Name": "is-default", "Values": ["true"]}]
        )
        if vpcs["Vpcs"]:
            vpc_id = vpcs["Vpcs"][0]["VpcId"]
            subnets = ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )
            if subnets["Subnets"]:
                return subnets["Subnets"][0]["SubnetId"]
    except (ClientError, BotoCoreError):
        pass

    try:
        subnets = ec2_client.describe_subnets()
        if subnets["Subnets"]:
            return subnets["Subnets"][0]["SubnetId"]
    except (ClientError, BotoCoreError):
        pass

    return None


def _get_latest_amazon_linux_ami(ec2_client: Any) -> str | None:
    """Resolve latest Amazon Linux 2023 x86_64 AMI."""
    try:
        response = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
            ],
        )
        images = sorted(
            response["Images"],
            key=lambda i: i.get("CreationDate", ""),
            reverse=True,
        )
        if images:
            return images[0]["ImageId"]
    except (ClientError, BotoCoreError):
        pass

    try:
        response = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
        images = sorted(
            response["Images"],
            key=lambda i: i.get("CreationDate", ""),
            reverse=True,
        )
        if images:
            return images[0]["ImageId"]
    except (ClientError, BotoCoreError):
        pass

    return None


def create_ebs_volume(ec2_client: Any, az: str) -> str | None:
    """Create unattached gp3 EBS volume."""
    print(f"\n  Creating unattached EBS volume ({EBS_SIZE_GB} GB, {EBS_VOLUME_TYPE}) ...")
    try:
        vol = ec2_client.create_volume(
            AvailabilityZone=az,
            Size=EBS_SIZE_GB,
            VolumeType=EBS_VOLUME_TYPE,
            TagSpecifications=[
                {
                    "ResourceType": "volume",
                    "Tags": COMMON_TAGS + [
                        {"Key": "Name", "Value": "cost-detective-orphan-volume"},
                        {"Key": "Purpose", "Value": "Demonstrate orphaned EBS waste"},
                    ],
                }
            ],
        )
        vol_id = vol["VolumeId"]
        print(f"     Volume created: {green(vol_id)}  (AZ: {az})")
        return vol_id
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Failed to create EBS volume: {exc}", file=sys.stderr)
        return None


def allocate_elastic_ip(ec2_client: Any) -> str | None:
    """Allocate Elastic IP without associating it."""
    print("\n  Allocating unassociated Elastic IP ...")
    try:
        eip = ec2_client.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {
                    "ResourceType": "elastic-ip",
                    "Tags": COMMON_TAGS + [
                        {"Key": "Name", "Value": "cost-detective-unused-eip"},
                        {"Key": "Purpose", "Value": "Demonstrate unused EIP waste"},
                    ],
                }
            ],
        )
        alloc_id = eip["AllocationId"]
        public_ip = eip.get("PublicIp", "n/a")
        print(f"     Elastic IP allocated: {green(alloc_id)}  (IP: {public_ip})")
        return alloc_id
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Failed to allocate EIP: {exc}", file=sys.stderr)
        return None


def launch_idle_instance(ec2_client: Any, subnet_id: str, ami_id: str) -> str | None:
    """Launch t3.micro EC2 instance with no workload."""
    print(f"\n  Launching idle EC2 instance ({EC2_INSTANCE_TYPE}) ...")
    try:
        resp = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType=EC2_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": COMMON_TAGS + [
                        {"Key": "Name", "Value": "idle-demo"},
                        {"Key": "Purpose", "Value": "Demonstrate idle instance waste"},
                    ],
                }
            ],
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        print(f"     Instance launched: {green(instance_id)}  (AMI: {ami_id})")
        return instance_id
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Failed to launch instance: {exc}", file=sys.stderr)
        return None


def cleanup_demo_resources(ec2_client: Any) -> int:
    """Find and delete all resources tagged Project=cost-detective-demo."""
    errors = 0
    tag_filter = [{"Name": f"tag:{DEMO_TAG_KEY}", "Values": [DEMO_TAG_VALUE]}]

    print("\n  Looking for demo EC2 instances ...")
    try:
        pages = ec2_client.get_paginator("describe_instances").paginate(Filters=tag_filter)
        instance_ids: list[str] = []
        for page in pages:
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    if inst["State"]["Name"] not in ("terminated", "shutting-down"):
                        instance_ids.append(inst["InstanceId"])

        if instance_ids:
            print(f"     Terminating {len(instance_ids)} instance(s): {', '.join(instance_ids)}")
            ec2_client.terminate_instances(InstanceIds=instance_ids)
            print("     Waiting for instances to begin terminating ...")
            waiter = ec2_client.get_waiter("instance_terminated")
            try:
                waiter.wait(
                    InstanceIds=instance_ids,
                    WaiterConfig={"Delay": 10, "MaxAttempts": 30},
                )
            except Exception:
                print(yellow("     WARNING: Timed out waiting - instances may still be shutting down."))
            print(f"     Instances terminated.")
        else:
            print("     No demo instances found.")
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Error scanning/terminating instances: {exc}", file=sys.stderr)
        errors += 1

    print("\n  Looking for demo EBS volumes ...")
    try:
        pages = ec2_client.get_paginator("describe_volumes").paginate(Filters=tag_filter)
        for page in pages:
            for vol in page["Volumes"]:
                vid = vol["VolumeId"]
                state = vol["State"]
                if state == "in-use":
                    print(f"     WARNING: Volume {vid} is in-use - skipping (detach first).")
                    errors += 1
                    continue
                if state != "available":
                    print(f"     Waiting for volume {vid} to become available (current: {state}) ...")
                    waiter = ec2_client.get_waiter("volume_available")
                    try:
                        waiter.wait(
                            VolumeIds=[vid],
                            WaiterConfig={"Delay": 5, "MaxAttempts": 24},
                        )
                    except Exception:
                        print(yellow(f"     WARNING: Timed out waiting for {vid} - attempting delete anyway."))
                print(f"     Deleting volume {vid} ...")
                try:
                    ec2_client.delete_volume(VolumeId=vid)
                    print(f"     Volume {vid} deleted.")
                except (ClientError, BotoCoreError) as exc:
                    print(f"     {red('ERROR')} Could not delete {vid}: {exc}", file=sys.stderr)
                    errors += 1
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Error scanning volumes: {exc}", file=sys.stderr)
        errors += 1

    print("\n  Looking for demo Elastic IPs ...")
    try:
        addrs = ec2_client.describe_addresses(Filters=tag_filter)
        for addr in addrs.get("Addresses", []):
            alloc_id = addr["AllocationId"]
            if addr.get("AssociationId"):
                print(f"     Disassociating EIP {alloc_id} ...")
                ec2_client.disassociate_address(AssociationId=addr["AssociationId"])
            print(f"     Releasing EIP {alloc_id} ...")
            try:
                ec2_client.release_address(AllocationId=alloc_id)
                print(f"     EIP {alloc_id} released.")
            except (ClientError, BotoCoreError) as exc:
                print(f"     {red('ERROR')} Could not release {alloc_id}: {exc}", file=sys.stderr)
                errors += 1
        if not addrs.get("Addresses"):
            print("     No demo Elastic IPs found.")
    except (ClientError, BotoCoreError) as exc:
        print(f"     {red('ERROR')} Error scanning EIPs: {exc}", file=sys.stderr)
        errors += 1

    return errors


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create or clean up demo wasteful AWS resources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --region eu-west-1\n"
            "  %(prog)s --region eu-west-1 --cleanup\n"
        ),
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region to target (default: use region from AWS config / environment).",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete all resources tagged with Project=cost-detective-demo instead of creating new ones.",
    )
    return parser.parse_args()


def main() -> int:
    """Main execution flow."""
    args = parse_args()

    try:
        session = boto3.Session(region_name=args.region)
        ec2 = session.client("ec2")
        effective_region = session.region_name or "unknown"
    except (BotoCoreError, ClientError) as exc:
        print(red(f"\n  ERROR: Failed to create AWS session: {exc}"), file=sys.stderr)
        return 2

    print(f"\n  {bold('Cost Detective — Sandbox Waste Manager')}")
    print(f"  Region: {bold(effective_region)}")

    if args.cleanup:
        print(f"  Mode  : {yellow('CLEANUP')}")
        print(f"  Filter: {DEMO_TAG_KEY}={DEMO_TAG_VALUE}")
        errs = cleanup_demo_resources(ec2)
        if errs:
            print(f"\n  {red('WARNING')} Cleanup finished with {errs} error(s).\n")
            return 1
        print(f"\n  {green('OK')} Cleanup complete - all demo resources removed.\n")
        return 0

    print(f"  Mode  : {green('CREATE')}")

    try:
        azs = ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )
        if not azs["AvailabilityZones"]:
            print(red("\n  ERROR: No available AZs found in this region."), file=sys.stderr)
            return 2
        chosen_az = azs["AvailabilityZones"][0]["ZoneName"]
    except (ClientError, BotoCoreError) as exc:
        print(red(f"\n  ERROR: Could not list AZs: {exc}"), file=sys.stderr)
        return 2

    subnet_id = _get_default_vpc_subnet(ec2)
    if not subnet_id:
        print(red("\n  ERROR: Could not find a suitable subnet."), file=sys.stderr)
        return 2

    ami_id = _get_latest_amazon_linux_ami(ec2)
    if not ami_id:
        print(red("\n  ERROR: Could not resolve an Amazon Linux AMI."), file=sys.stderr)
        return 2

    created: dict[str, str | None] = {}

    created["ebs_volume"] = create_ebs_volume(ec2, chosen_az)
    created["elastic_ip"] = allocate_elastic_ip(ec2)
    created["ec2_instance"] = launch_idle_instance(ec2, subnet_id, ami_id)

    # --- Summary ---
    print(f"\n  {'─' * 55}")
    print(bold("  Summary of created resources:"))
    print(f"  {'─' * 55}")
    for label, rid in created.items():
        status = green(rid) if rid else red("FAILED")
        print(f"    {label:<20} : {status}")
    print(f"  {'─' * 55}")

    failures = sum(1 for v in created.values() if v is None)
    if failures:
        print(f"\n  {yellow('WARNING')} {failures} resource(s) failed to create.")
        print(f"  Run with {bold('--cleanup')} to tear down partial resources.\n")
        return 1

    print(f"\n  {green('OK')} All demo resources created successfully.")
    print(f"  Run with {bold('--cleanup')} when you are done to avoid charges.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
