#!/usr/bin/env python3
"""
tagging_compliance.py — AWS Resource Tagging Compliance Scanner

Scans EC2 instances, EBS volumes, and Elastic IPs for required cost-allocation tags.
Produces a terminal table and optional JSON report.

Required tags: CostCenter, Environment, Owner, Project

Usage:
  python tagging_compliance.py --region eu-west-1
  python tagging_compliance.py --region eu-west-1 --output report.json

Exit codes:
  0 – all resources are fully compliant
  1 – at least one resource is missing a required tag
  2 – a runtime / AWS API error occurred
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


REQUIRED_TAGS: list[str] = [
    "CostCenter",
    "Environment",
    "Owner",
    "Project",
]

_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def green(text: str) -> str:
    return _c("32", text)


def red(text: str) -> str:
    return _c("31", text)


def yellow(text: str) -> str:
    return _c("33", text)


def bold(text: str) -> str:
    return _c("1", text)


def _tags_to_dict(tag_list: list[dict[str, str]] | None) -> dict[str, str]:
    """Convert AWS tag list to dict."""
    if not tag_list:
        return {}
    return {t["Key"]: t["Value"] for t in tag_list}


def _check_compliance(tags: dict[str, str]) -> tuple[bool, list[str]]:
    """Return (is_compliant, missing_tags) for a resource."""
    missing = [t for t in REQUIRED_TAGS if t not in tags]
    return (len(missing) == 0), missing


def scan_ec2_instances(ec2_client: Any) -> list[dict[str, Any]]:
    """Scan all EC2 instances and evaluate tagging compliance."""
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_instances")

    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                tags = _tags_to_dict(inst.get("Tags"))
                compliant, missing = _check_compliance(tags)
                results.append({
                    "resource_type": "EC2 Instance",
                    "resource_id": inst["InstanceId"],
                    "name": tags.get("Name", "—"),
                    "state": inst["State"]["Name"],
                    "tags": tags,
                    "compliant": compliant,
                    "missing_tags": missing,
                })

    return results


def scan_ebs_volumes(ec2_client: Any) -> list[dict[str, Any]]:
    """Scan all EBS volumes and evaluate tagging compliance."""
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_volumes")

    for page in paginator.paginate():
        for vol in page["Volumes"]:
            tags = _tags_to_dict(vol.get("Tags"))
            compliant, missing = _check_compliance(tags)
            attachment_state = "unattached"
            if vol.get("Attachments"):
                attachment_state = vol["Attachments"][0].get("State", "unknown")
            results.append({
                "resource_type": "EBS Volume",
                "resource_id": vol["VolumeId"],
                "name": tags.get("Name", "—"),
                "state": attachment_state,
                "tags": tags,
                "compliant": compliant,
                "missing_tags": missing,
            })

    return results


def scan_elastic_ips(ec2_client: Any) -> list[dict[str, Any]]:
    """Scan all Elastic IPs and evaluate tagging compliance."""
    results: list[dict[str, Any]] = []
    response = ec2_client.describe_addresses()

    for addr in response.get("Addresses", []):
        tags = _tags_to_dict(addr.get("Tags"))
        compliant, missing = _check_compliance(tags)
        association = "associated" if addr.get("AssociationId") else "unassociated"
        results.append({
            "resource_type": "Elastic IP",
            "resource_id": addr.get("AllocationId", addr.get("PublicIp", "unknown")),
            "name": tags.get("Name", addr.get("PublicIp", "—")),
            "state": association,
            "tags": tags,
            "compliant": compliant,
            "missing_tags": missing,
        })

    return results


def _print_table(results: list[dict[str, Any]], region: str) -> None:
    """Print compliance table to stdout."""
    print()
    print(bold(f"  Cost Detective — Tagging Compliance Report"))
    print(bold(f"  Region: {region}"))
    print(bold(f"  Scanned at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"))
    print(bold(f"  Required tags: {', '.join(REQUIRED_TAGS)}"))
    print()

    if not results:
        print(yellow("  WARNING: No resources found in this region.\n"))
        return
    col_type = max(len("Resource Type"), max(len(r["resource_type"]) for r in results))
    col_id = max(len("Resource ID"), max(len(r["resource_id"]) for r in results))
    col_name = max(len("Name"), max(len(r["name"]) for r in results))
    col_state = max(len("State"), max(len(r["state"]) for r in results))
    col_status = len("Compliant?")
    col_missing = len("Missing Tags")

    header = (
        f"  {'Resource Type':<{col_type}}  "
        f"{'Resource ID':<{col_id}}  "
        f"{'Name':<{col_name}}  "
        f"{'State':<{col_state}}  "
        f"{'Compliant?':<{col_status}}  "
        f"Missing Tags"
    )
    separator = "  " + "-" * (len(header) - 2)

    print(bold(header))
    print(separator)

    for r in results:
        status_str = green("YES") if r["compliant"] else red("NO")
        missing_str = ", ".join(r["missing_tags"]) if r["missing_tags"] else "—"
        print(
            f"  {r['resource_type']:<{col_type}}  "
            f"{r['resource_id']:<{col_id}}  "
            f"{r['name']:<{col_name}}  "
            f"{r['state']:<{col_state}}  "
            f"{status_str:<{col_status + (len(status_str) - len('✔ YES'))}}  "
            f"{missing_str}"
        )

    print(separator)

    total = len(results)
    compliant_count = sum(1 for r in results if r["compliant"])
    non_compliant_count = total - compliant_count
    pct = (compliant_count / total * 100) if total else 0.0

    colour_fn = green if pct == 100 else (yellow if pct >= 75 else red)

    print()
    print(f"  Total resources scanned : {bold(str(total))}")
    print(f"  Compliant               : {green(str(compliant_count))}")
    print(f"  Non-compliant           : {red(str(non_compliant_count))}")
    print(f"  Compliance rate         : {colour_fn(f'{pct:.1f}%')}")
    print()


def _build_json_report(results: list[dict[str, Any]], region: str) -> dict[str, Any]:
    """Build JSON-serializable compliance report."""
    total = len(results)
    compliant_count = sum(1 for r in results if r["compliant"])

    return {
        "report_name": "Cost Detective — Tagging Compliance Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "required_tags": REQUIRED_TAGS,
        "summary": {
            "total_resources": total,
            "compliant": compliant_count,
            "non_compliant": total - compliant_count,
            "compliance_percentage": round(compliant_count / total * 100, 2) if total else 0.0,
        },
        "resources": [
            {
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "name": r["name"],
                "state": r["state"],
                "compliant": r["compliant"],
                "missing_tags": r["missing_tags"],
                "current_tags": r["tags"],
            }
            for r in results
        ],
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Scan AWS resources for tagging compliance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --region eu-west-1\n"
            "  %(prog)s --region eu-west-1 --output report.json\n"
        ),
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region to scan (default: use region from AWS config / environment).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Path to write a JSON compliance report. If omitted, results are printed to the terminal only.",
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
        print(red(f"  ERROR: Failed to create AWS session: {exc}"), file=sys.stderr)
        return 2

    print(f"\n  Scanning resources in {bold(effective_region)} …")
    all_results: list[dict[str, Any]] = []

    try:
        print("    → EC2 instances …", end=" ", flush=True)
        ec2_results = scan_ec2_instances(ec2)
        print(f"found {len(ec2_results)}")
        all_results.extend(ec2_results)

        print("    -> EBS volumes ...", end=" ", flush=True)
        ebs_results = scan_ebs_volumes(ec2)
        print(f"found {len(ebs_results)}")
        all_results.extend(ebs_results)

        print("    -> Elastic IPs ...", end=" ", flush=True)
        eip_results = scan_elastic_ips(ec2)
        print(f"found {len(eip_results)}")
        all_results.extend(eip_results)

    except ClientError as exc:
        print(red(f"\n  ERROR: AWS API error: {exc}"), file=sys.stderr)
        return 2
    except BotoCoreError as exc:
        print(red(f"\n  ERROR: AWS SDK error: {exc}"), file=sys.stderr)
        return 2

    _print_table(all_results, effective_region)

    if args.output:
        report = _build_json_report(all_results, effective_region)
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, default=str)
            print(f"  JSON report written to: {bold(args.output)}\n")
        except OSError as exc:
            print(red(f"  ERROR: Could not write report: {exc}"), file=sys.stderr)
            return 2

    non_compliant = any(not r["compliant"] for r in all_results)
    return 1 if non_compliant else 0


if __name__ == "__main__":
    raise SystemExit(main())
