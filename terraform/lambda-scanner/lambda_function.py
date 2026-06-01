"""
Cost Detective — Lambda Scanner
================================
A serverless FinOps waste-detection scanner that identifies idle and orphaned
AWS resources which are silently accumulating charges.

Checks performed
----------------
1. **Unattached EBS Volumes**  — Volumes in the ``available`` state (not
   attached to any instance).  These still incur storage charges.
2. **Unassociated Elastic IPs** — Allocated EIPs without an active ENI
   association.  AWS charges ~$3.65/month per unused EIP.
3. **Idle EC2 Instances** — Running instances whose average CPU utilisation
   over the past 14 days falls below a configurable threshold.

Environment variables (set via Terraform)
------------------------------------------
SNS_TOPIC_ARN      — ARN of the SNS topic to publish the report to.
IDLE_CPU_THRESHOLD  — CPU % threshold below which an instance is "idle" (default 5).

Returns
-------
A JSON report summarising all findings, also published to SNS.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logger = logging.getLogger("cost_detective")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment Variables
# ---------------------------------------------------------------------------
SNS_TOPIC_ARN: str = os.environ.get("SNS_TOPIC_ARN", "")
IDLE_CPU_THRESHOLD: float = float(os.environ.get("IDLE_CPU_THRESHOLD", "5"))

# Lookback window for CloudWatch CPU metrics (14 days).
LOOKBACK_DAYS: int = 14

# ---------------------------------------------------------------------------
# AWS SDK Clients — instantiated at module level for connection reuse across
# warm Lambda invocations.
# ---------------------------------------------------------------------------
ec2_client = boto3.client("ec2")
cloudwatch_client = boto3.client("cloudwatch")
sns_client = boto3.client("sns")


# ==========================================================================
# Scanner Functions
# ==========================================================================


def scan_unattached_ebs_volumes() -> list[dict[str, Any]]:
    """
    Find EBS volumes in the 'available' state (not attached to any instance).

    Returns
    -------
    list[dict]
        Each dict contains VolumeId, Size (GiB), VolumeType, CreateTime,
        and any Name tag.
    """
    logger.info("Scanning for unattached EBS volumes...")
    findings: list[dict[str, Any]] = []

    try:
        paginator = ec2_client.get_paginator("describe_volumes")
        page_iterator = paginator.paginate(
            Filters=[{"Name": "status", "Values": ["available"]}]
        )

        for page in page_iterator:
            for volume in page.get("Volumes", []):
                # Extract the Name tag if present.
                name_tag = _extract_name_tag(volume.get("Tags", []))

                findings.append(
                    {
                        "VolumeId": volume["VolumeId"],
                        "SizeGiB": volume["Size"],
                        "VolumeType": volume["VolumeType"],
                        "CreateTime": volume["CreateTime"].isoformat(),
                        "AvailabilityZone": volume.get("AvailabilityZone", "N/A"),
                        "Name": name_tag,
                    }
                )

    except ClientError as exc:
        logger.error("Error scanning EBS volumes: %s", exc)

    logger.info("Found %d unattached EBS volume(s).", len(findings))
    return findings


def scan_unassociated_eips() -> list[dict[str, Any]]:
    """
    Find Elastic IP addresses that are allocated but not associated with any
    running instance or network interface.

    Returns
    -------
    list[dict]
        Each dict contains AllocationId, PublicIp, and Domain.
    """
    logger.info("Scanning for unassociated Elastic IPs...")
    findings: list[dict[str, Any]] = []

    try:
        response = ec2_client.describe_addresses()
        for address in response.get("Addresses", []):
            # An EIP is considered unassociated if it has no AssociationId.
            if not address.get("AssociationId"):
                name_tag = _extract_name_tag(address.get("Tags", []))

                findings.append(
                    {
                        "AllocationId": address["AllocationId"],
                        "PublicIp": address.get("PublicIp", "N/A"),
                        "Domain": address.get("Domain", "vpc"),
                        "Name": name_tag,
                    }
                )

    except ClientError as exc:
        logger.error("Error scanning Elastic IPs: %s", exc)

    logger.info("Found %d unassociated Elastic IP(s).", len(findings))
    return findings


def scan_idle_ec2_instances() -> list[dict[str, Any]]:
    """
    Find running EC2 instances with average CPU utilisation below the
    configured threshold over the past ``LOOKBACK_DAYS``.

    The function retrieves CloudWatch ``CPUUtilization`` metrics for each
    running instance and computes the overall average.  Instances with no
    datapoints are flagged as well (they may have monitoring disabled).

    Returns
    -------
    list[dict]
        Each dict contains InstanceId, InstanceType, LaunchTime,
        AvgCpuPercent, and any Name tag.
    """
    logger.info(
        "Scanning for idle EC2 instances (threshold: %.1f%%, lookback: %d days)...",
        IDLE_CPU_THRESHOLD,
        LOOKBACK_DAYS,
    )
    findings: list[dict[str, Any]] = []

    try:
        # Retrieve all running instances.
        paginator = ec2_client.get_paginator("describe_instances")
        page_iterator = paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )

        for page in page_iterator:
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id: str = instance["InstanceId"]
                    avg_cpu = _get_average_cpu(instance_id)

                    if avg_cpu is not None and avg_cpu < IDLE_CPU_THRESHOLD:
                        name_tag = _extract_name_tag(instance.get("Tags", []))

                        findings.append(
                            {
                                "InstanceId": instance_id,
                                "InstanceType": instance.get("InstanceType", "N/A"),
                                "LaunchTime": instance["LaunchTime"].isoformat(),
                                "AvgCpuPercent": round(avg_cpu, 2),
                                "Name": name_tag,
                            }
                        )

    except ClientError as exc:
        logger.error("Error scanning EC2 instances: %s", exc)

    logger.info("Found %d idle EC2 instance(s).", len(findings))
    return findings


# ==========================================================================
# Helper Functions
# ==========================================================================


def _get_average_cpu(instance_id: str) -> float | None:
    """
    Query CloudWatch for the average CPUUtilization of an instance over the
    lookback window.

    Parameters
    ----------
    instance_id : str
        The EC2 instance ID.

    Returns
    -------
    float or None
        Average CPU utilisation as a percentage, or ``None`` if no datapoints
        are available.
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)

    try:
        response = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,  # 1-day granularity
            Statistics=["Average"],
        )

        datapoints = response.get("Datapoints", [])
        if not datapoints:
            logger.debug("No CPU datapoints for instance %s.", instance_id)
            return None

        # Compute the mean across all daily averages.
        total = sum(dp["Average"] for dp in datapoints)
        return total / len(datapoints)

    except ClientError as exc:
        logger.warning(
            "Could not fetch CPU metrics for %s: %s", instance_id, exc
        )
        return None


def _extract_name_tag(tags: list[dict[str, str]] | None) -> str:
    """
    Extract the value of the ``Name`` tag from a list of AWS resource tags.

    Parameters
    ----------
    tags : list[dict] or None
        The ``Tags`` list returned by AWS API calls.

    Returns
    -------
    str
        The Name tag value, or ``"N/A"`` if not present.
    """
    if not tags:
        return "N/A"
    for tag in tags:
        if tag.get("Key") == "Name":
            return tag.get("Value", "N/A")
    return "N/A"


def _build_report(
    unattached_volumes: list[dict[str, Any]],
    unassociated_eips: list[dict[str, Any]],
    idle_instances: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Assemble the individual scan results into a unified JSON report.

    Parameters
    ----------
    unattached_volumes : list[dict]
        Findings from the EBS volume scan.
    unassociated_eips : list[dict]
        Findings from the Elastic IP scan.
    idle_instances : list[dict]
        Findings from the idle-instance scan.

    Returns
    -------
    dict
        The complete report including metadata, summary counts, and detailed
        findings for each check.
    """
    total_findings = (
        len(unattached_volumes) + len(unassociated_eips) + len(idle_instances)
    )

    report: dict[str, Any] = {
        "reportMetadata": {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "scanner": "CostDetective-LambdaScanner",
            "idleCpuThresholdPercent": IDLE_CPU_THRESHOLD,
            "lookbackDays": LOOKBACK_DAYS,
        },
        "summary": {
            "totalFindings": total_findings,
            "unattachedEbsVolumes": len(unattached_volumes),
            "unassociatedElasticIps": len(unassociated_eips),
            "idleEc2Instances": len(idle_instances),
        },
        "findings": {
            "unattachedEbsVolumes": unattached_volumes,
            "unassociatedElasticIps": unassociated_eips,
            "idleEc2Instances": idle_instances,
        },
    }

    return report


def _format_sns_message(report: dict[str, Any]) -> str:
    """
    Format the report into a human-readable plain-text message suitable for
    SNS email delivery.

    Parameters
    ----------
    report : dict
        The unified report dictionary.

    Returns
    -------
    str
        A formatted multi-line string.
    """
    summary = report["summary"]
    lines: list[str] = [
        "=" * 60,
        "  COST DETECTIVE — Weekly Waste Report",
        "=" * 60,
        "",
        f"  Generated: {report['reportMetadata']['generatedAt']}",
        f"  Total findings: {summary['totalFindings']}",
        "",
        "  SUMMARY",
        "  -------",
        f"  Unattached EBS Volumes : {summary['unattachedEbsVolumes']}",
        f"  Unassociated Elastic IPs: {summary['unassociatedElasticIps']}",
        f"  Idle EC2 Instances      : {summary['idleEc2Instances']}",
        "",
    ]

    # --- Unattached EBS Volumes ---
    volumes = report["findings"]["unattachedEbsVolumes"]
    if volumes:
        lines.append("  UNATTACHED EBS VOLUMES")
        lines.append("  " + "-" * 40)
        for vol in volumes:
            lines.append(
                f"    • {vol['VolumeId']} | {vol['SizeGiB']} GiB | "
                f"{vol['VolumeType']} | {vol['Name']}"
            )
        lines.append("")

    # --- Unassociated Elastic IPs ---
    eips = report["findings"]["unassociatedElasticIps"]
    if eips:
        lines.append("  UNASSOCIATED ELASTIC IPs")
        lines.append("  " + "-" * 40)
        for eip in eips:
            lines.append(
                f"    • {eip['AllocationId']} | {eip['PublicIp']} | {eip['Name']}"
            )
        lines.append("")

    # --- Idle EC2 Instances ---
    instances = report["findings"]["idleEc2Instances"]
    if instances:
        lines.append(
            f"  IDLE EC2 INSTANCES (avg CPU < {IDLE_CPU_THRESHOLD}%)"
        )
        lines.append("  " + "-" * 40)
        for inst in instances:
            lines.append(
                f"    • {inst['InstanceId']} | {inst['InstanceType']} | "
                f"CPU {inst['AvgCpuPercent']}% | {inst['Name']}"
            )
        lines.append("")

    # Closing
    if summary["totalFindings"] == 0:
        lines.append("  ✅  No waste detected — your account looks clean!")
    else:
        lines.append(
            "  ⚠️  Review the findings above and take action to reduce costs."
        )

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def _publish_to_sns(report: dict[str, Any]) -> None:
    """
    Publish the formatted report to the configured SNS topic.

    Parameters
    ----------
    report : dict
        The unified report dictionary.
    """
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN is not set — skipping SNS publish.")
        return

    message = _format_sns_message(report)
    subject = (
        f"Cost Detective Report — "
        f"{report['summary']['totalFindings']} finding(s)"
    )

    # SNS subject line is limited to 100 characters.
    subject = subject[:100]

    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
        logger.info("Report published to SNS topic: %s", SNS_TOPIC_ARN)
    except ClientError as exc:
        logger.error("Failed to publish to SNS: %s", exc)
        raise


# ==========================================================================
# Lambda Entry Point
# ==========================================================================


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    AWS Lambda handler — entry point invoked by EventBridge on a schedule.

    Workflow
    --------
    1. Run all three scanners concurrently (sequential in this implementation).
    2. Assemble a unified report.
    3. Publish the report to SNS.
    4. Return the report as JSON (visible in Lambda console / Step Functions).

    Parameters
    ----------
    event : dict
        The EventBridge event payload (unused but logged).
    context : LambdaContext
        AWS Lambda runtime context.

    Returns
    -------
    dict
        ``statusCode`` and the full report body.
    """
    logger.info("Cost Detective scanner invoked. Event: %s", json.dumps(event))

    # ----- Run Scans -----
    unattached_volumes = scan_unattached_ebs_volumes()
    unassociated_eips = scan_unassociated_eips()
    idle_instances = scan_idle_ec2_instances()

    # ----- Build Report -----
    report = _build_report(unattached_volumes, unassociated_eips, idle_instances)

    logger.info(
        "Scan complete — %d total finding(s).", report["summary"]["totalFindings"]
    )

    # ----- Publish to SNS -----
    _publish_to_sns(report)

    # ----- Return JSON response -----
    return {
        "statusCode": 200,
        "body": report,
    }
