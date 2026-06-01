#!/usr/bin/env python3
"""
zombie_hunter.py — AWS Zombie Resource Detector & Garbage Collector

Detects and optionally deletes zombie AWS resources:
  - Unattached EBS volumes
  - Unassociated Elastic IP addresses
  - Idle EC2 instances (low CPU AND low network over a lookback window)

Before deleting anything, each resource is investigated in CloudTrail and
placed in a lifecycle tier by how long it has been idle. ONLY the SAFE tier
(idle >= --decommission-days) is ever eligible for deletion.

Usage:
  python zombie_hunter.py --dry-run
  python zombie_hunter.py --execute
  python zombie_hunter.py --region eu-west-1 --output report.json
  python zombie_hunter.py --execute --yes            # non-interactive
  python zombie_hunter.py --dry-run --skip-cloudtrail # state scan only
"""

import sys
import boto3
import argparse
import json
import datetime
from botocore.config import Config
from botocore.exceptions import ClientError


def _utcnow():
    """Timezone-aware current UTC time (utcnow() is deprecated on 3.12+)."""
    return datetime.datetime.now(datetime.timezone.utc)


DEFAULT_REGION = "eu-west-1"

# --- Idle EC2 detection -----------------------------------------------------
# An instance is an idle CANDIDATE only when BOTH CPU and network are low over
# the lookback window. CPU alone is a weak signal: a low-CPU box can still be a
# busy DNS resolver, license server, or proxy -- the network check guards that.
IDLE_CPU_THRESHOLD = 5.0                 # percent
DEFAULT_NETWORK_IDLE_MB_PER_DAY = 5.0    # combined NetworkIn+NetworkOut MB/day
DEFAULT_IDLE_LOOKBACK_DAYS = 14

# ---------------------------------------------------------------------------
# CloudTrail lifecycle tiers (by days since last *mutating* API activity).
# A resource is investigated in CloudTrail before any deletion; its idle age
# places it in a tier. ONLY the SAFE tier may be deleted:
#
#   tagged exempt              -> EXEMPT          never deleted
#   idle <  flag_days          -> ACTIVE          recently used, protected
#   flag_days <= idle < esc.   -> FLAG            review / alert owner
#   esc_days  <= idle < decom  -> ESCALATE        likely idle
#   idle >= decommission_days  -> SAFE            confirmed waste (deletable)
#   no mutating activity found -> INCONCLUSIVE    ambiguous, protected*
#   CloudTrail error / denied  -> UNVERIFIABLE    fail-safe, protected
#   --skip-cloudtrail          -> NOT_INVESTIGATED protected
#
# * INCONCLUSIVE is only promoted to SAFE when --treat-no-activity-as-idle is
#   passed (you trust your CloudTrail coverage). Absence of evidence is not,
#   by default, evidence of idleness.
#
# CloudTrail retains ~90 days of management events; activity older than the
# lookback window is invisible, which is exactly why "no activity" is ambiguous.
# ---------------------------------------------------------------------------
DEFAULT_FLAG_DAYS = 7
DEFAULT_ESCALATE_DAYS = 14
DEFAULT_DECOMMISSION_DAYS = 30
CLOUDTRAIL_LOOKBACK_DAYS = 90
DEFAULT_EXEMPT_TAG = "DoNotDelete"

# Safeguarded decommissioning: a snapshot (EBS) or AMI (EC2) is taken before
# deletion. We wait up to this many seconds for it to complete, and abort the
# delete if it does not. The retention value is tagged onto backups so a
# lifecycle policy can expire them later.
DEFAULT_SNAPSHOT_WAIT_TIMEOUT = 600
DEFAULT_SNAPSHOT_RETENTION_DAYS = 30

# Events that mean "the resource was created" -- if this is the only/last
# activity, the resource was provisioned and never used since.
CREATION_EVENTS = {"CreateVolume", "RunInstances", "AllocateAddress"}

TIER_EXEMPT = "EXEMPT"
TIER_ACTIVE = "ACTIVE"
TIER_FLAG = "FLAG"
TIER_ESCALATE = "ESCALATE"
TIER_SAFE = "SAFE"
TIER_INCONCLUSIVE = "INCONCLUSIVE"
TIER_UNVERIFIABLE = "UNVERIFIABLE"
TIER_NOT_INVESTIGATED = "NOT_INVESTIGATED"

TIER_LABEL = {
    TIER_EXEMPT: "EXEMPT            exemption tag present - never deleted",
    TIER_ACTIVE: "ACTIVE            recently used - protected",
    TIER_FLAG: "FLAG              review / alert owner - NOT deleted",
    TIER_ESCALATE: "ESCALATE          likely idle - NOT deleted",
    TIER_INCONCLUSIVE: "INCONCLUSIVE      no activity found (ambiguous) - protected",
    TIER_UNVERIFIABLE: "UNVERIFIABLE      CloudTrail unavailable - protected",
    TIER_NOT_INVESTIGATED: "NOT_INVESTIGATED  CloudTrail skipped - protected",
    TIER_SAFE: "SAFE              confirmed waste - eligible for deletion",
}

# Display order: most-protected first, the only deletable tier (SAFE) last.
TIER_ORDER = [
    TIER_EXEMPT, TIER_ACTIVE, TIER_FLAG, TIER_ESCALATE,
    TIER_INCONCLUSIVE, TIER_UNVERIFIABLE, TIER_NOT_INVESTIGATED, TIER_SAFE,
]


def get_clients(region):
    """Initialize AWS service clients.

    CloudTrail's lookup_events is rate-limited (~2 req/s); adaptive retries let
    the SDK self-throttle instead of erroring out on busy accounts.
    """
    retry_cfg = Config(retries={"max_attempts": 10, "mode": "adaptive"})
    return {
        "ec2": boto3.client("ec2", region_name=region),
        "cloudwatch": boto3.client("cloudwatch", region_name=region),
        "cloudtrail": boto3.client("cloudtrail", region_name=region, config=retry_cfg),
        "sts": boto3.client("sts", region_name=region),
    }


# ===========================================================================
# Detection
# ===========================================================================


def find_unattached_ebs(ec2_client):
    """Find EBS volumes in 'available' state.

    A detached volume cannot serve data-plane I/O, so its 'available' state is
    itself strong evidence of waste; CloudTrail recency guards against volumes
    detached only moments ago.
    """
    print("\nScanning for unattached EBS volumes...")

    zombies = []
    paginator = ec2_client.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for volume in page["Volumes"]:
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
                # Public IP kept separately: CloudTrail may index EIP events
                # under the public IP rather than the allocation ID.
                "public_ip": eip,
                "details": f"Public IP: {eip}",
                "created": "N/A",
                "monthly_cost_usd": 3.65,
                "tags": tags,
            })
            print(f"  WARNING: EIP {eip} | Allocation ID: {alloc_id} | ~$3.65/mo")

    if not zombies:
        print("  OK: No unassociated Elastic IPs found.")
    return zombies


def get_average_cpu(cw_client, instance_id, days):
    """Average CPUUtilization (%) over the past N days.

    Returns None when there are no datapoints (just-launched instance or
    detailed monitoring off) or the call fails. None means "unknown" -- never
    "idle".
    """
    end_time = _utcnow()
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


def get_avg_network_bytes_per_day(cw_client, instance_id, days):
    """Average combined NetworkIn+NetworkOut bytes/day over the past N days.

    Returns None when no datapoints are available or the call fails. A low-CPU
    instance that still pushes meaningful network traffic is NOT idle.
    """
    end_time = _utcnow()
    start_time = end_time - datetime.timedelta(days=days)
    total_per_day = 0.0
    have_data = False
    for metric in ("NetworkIn", "NetworkOut"):
        try:
            response = cw_client.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName=metric,
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=["Sum"],
            )
            datapoints = response.get("Datapoints", [])
            if datapoints:
                have_data = True
                total_per_day += sum(d["Sum"] for d in datapoints) / len(datapoints)
        except ClientError:
            return None
    return total_per_day if have_data else None


def find_idle_ec2_instances(ec2_client, cw_client, cpu_threshold,
                            network_idle_mb_per_day, lookback_days):
    """Find running EC2 instances that are idle by BOTH CPU and network."""
    print(f"\nScanning for idle EC2 instances "
          f"(CPU < {cpu_threshold}% AND network < {network_idle_mb_per_day} MB/day "
          f"over {lookback_days} days)...")

    net_threshold_bytes = network_idle_mb_per_day * 1024 * 1024
    zombies = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_type = instance["InstanceType"]
                launch_time = instance["LaunchTime"].strftime("%Y-%m-%d")
                tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                name = tags.get("Name", "Unnamed")

                avg_cpu = get_average_cpu(cw_client, instance_id, lookback_days)
                if avg_cpu is None:
                    print(f"  SKIP: Instance {instance_id} ({instance_type}) | "
                          f"Name: {name} | No CPU data - not classified as idle")
                    continue
                if avg_cpu >= cpu_threshold:
                    continue  # not even CPU-idle

                net = get_avg_network_bytes_per_day(cw_client, instance_id, lookback_days)
                net_mb = round(net / 1024 / 1024, 2) if net is not None else None

                # Low CPU but real network traffic => NOT idle (busy service).
                if net is not None and net >= net_threshold_bytes:
                    print(f"  SKIP: Instance {instance_id} ({instance_type}) | Name: {name} | "
                          f"CPU {avg_cpu}% but network {net_mb} MB/day - active, not idle")
                    continue

                net_desc = f"{net_mb} MB/day" if net_mb is not None else "no network data"
                zombies.append({
                    "resource_type": "EC2 Instance",
                    "resource_id": instance_id,
                    "details": f"{instance_type} | Name: {name} | "
                               f"Avg CPU: {avg_cpu}% | Network: {net_desc}",
                    "created": launch_time,
                    "monthly_cost_usd": "varies",
                    "tags": tags,
                })
                print(f"  WARNING: Instance {instance_id} ({instance_type}) | Name: {name} | "
                      f"CPU {avg_cpu}% | {net_desc}")

    if not zombies:
        print("  OK: No idle EC2 instances found.")
    return zombies


# ===========================================================================
# Safeguarded decommissioning (backup-before-delete)
# ===========================================================================


def _backup_tags(source_kind, source_id, actor, retention_days):
    """Provenance tags applied to snapshots / AMIs created before deletion."""
    return [
        {"Key": "Name", "Value": f"cost-detective-backup-{source_id}"},
        {"Key": "CostDetective:Backup", "Value": "true"},
        {"Key": "CostDetective:SourceKind", "Value": source_kind},
        {"Key": "CostDetective:SourceId", "Value": source_id},
        {"Key": "CostDetective:DeletedBy", "Value": str(actor)[:255]},
        {"Key": "CostDetective:DeletedAt", "Value": _utcnow().isoformat()},
        {"Key": "CostDetective:Reason", "Value": "zombie-cleanup"},
        {"Key": "CostDetective:RetentionDays", "Value": str(retention_days)},
    ]


def snapshot_volume(ec2_client, volume_id, wait, timeout, retention_days, actor):
    """Snapshot an EBS volume before deletion.

    With wait=True, blocks until the snapshot is 'completed' and returns its ID.
    Returns None on any failure -- the caller MUST then skip the delete.
    """
    try:
        snap_id = ec2_client.create_snapshot(
            VolumeId=volume_id,
            Description=f"Cost Detective pre-deletion backup of {volume_id}",
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": _backup_tags("EBS Volume", volume_id, actor, retention_days),
            }],
        )["SnapshotId"]
        print(f"    snapshot {snap_id} started for {volume_id}")
    except ClientError as e:
        print(f"    ERROR: could not snapshot {volume_id} ({e}) - delete skipped")
        return None

    if wait:
        try:
            delay = 15
            ec2_client.get_waiter("snapshot_completed").wait(
                SnapshotIds=[snap_id],
                WaiterConfig={"Delay": delay, "MaxAttempts": max(1, timeout // delay)},
            )
            print(f"    snapshot {snap_id} completed")
        except Exception as e:  # WaiterError on timeout/failure
            print(f"    ERROR: snapshot {snap_id} did not complete ({e}) - delete skipped")
            return None
    return snap_id


def image_instance(ec2_client, instance_id, wait, timeout, retention_days, actor):
    """Create an AMI of an instance before termination.

    With wait=True, blocks until the AMI is 'available' and returns its ID.
    Returns None on failure -- the caller MUST then skip the terminate.
    """
    try:
        ami_id = ec2_client.create_image(
            InstanceId=instance_id,
            Name=f"cost-detective-{instance_id}-{_utcnow().strftime('%Y%m%d%H%M%S')}",
            Description=f"Cost Detective pre-termination backup of {instance_id}",
            NoReboot=True,  # don't disrupt; the instance is being removed anyway
            TagSpecifications=[
                {"ResourceType": "image",
                 "Tags": _backup_tags("EC2 Instance", instance_id, actor, retention_days)},
                {"ResourceType": "snapshot",
                 "Tags": _backup_tags("EC2 Instance", instance_id, actor, retention_days)},
            ],
        )["ImageId"]
        print(f"    AMI {ami_id} started for {instance_id}")
    except ClientError as e:
        print(f"    ERROR: could not image {instance_id} ({e}) - terminate skipped")
        return None

    if wait:
        try:
            delay = 15
            ec2_client.get_waiter("image_available").wait(
                ImageIds=[ami_id],
                WaiterConfig={"Delay": delay, "MaxAttempts": max(1, timeout // delay)},
            )
            print(f"    AMI {ami_id} available")
        except Exception as e:
            print(f"    ERROR: AMI {ami_id} not available ({e}) - terminate skipped")
            return None
    return ami_id


def _record(resource, action, status, **extra):
    """Build one decommission-manifest record."""
    rec = {
        "resource_id": resource["resource_id"],
        "resource_type": resource["resource_type"],
        "action": action,
        "status": status,
        "backup_id": None,
        "rollback": None,
        "detail": None,
    }
    rec.update(extra)
    return rec


def _decommission_ebs(ec2_client, z, opts):
    """Race-check, snapshot, then delete one EBS volume."""
    rid = z["resource_id"]
    try:
        vol = ec2_client.describe_volumes(VolumeIds=[rid])["Volumes"][0]
        if vol["State"] != "available":
            print(f"  SKIP volume {rid}: no longer available (state={vol['State']})")
            return _record(z, "skip", "skipped", detail=f"state={vol['State']}")
    except ClientError as e:
        print(f"  SKIP volume {rid}: re-check failed ({e})")
        return _record(z, "skip", "error", detail=str(e))

    backup_id = None
    if opts["backup"]:
        backup_id = snapshot_volume(ec2_client, rid, opts["wait"], opts["timeout"],
                                    opts["retention"], opts["actor"])
        if backup_id is None:
            return _record(z, "snapshot+delete", "aborted",
                           detail="backup failed; volume NOT deleted")
    try:
        ec2_client.delete_volume(VolumeId=rid)
        print(f"  DELETED volume {rid}"
              + (f" (backup {backup_id})" if backup_id else " (no backup)"))
        rollback = (f"aws ec2 create-volume --snapshot-id {backup_id} "
                    "--availability-zone <az> --region <region>") if backup_id else None
        return _record(z, "snapshot+delete" if backup_id else "delete", "done",
                       backup_id=backup_id, rollback=rollback)
    except ClientError as e:
        print(f"  ERROR deleting volume {rid}: {e}")
        return _record(z, "delete", "error", backup_id=backup_id, detail=str(e))


def _decommission_eip(ec2_client, z, opts):
    """Release one Elastic IP (recorded for re-allocation; cannot be backed up)."""
    rid = z["resource_id"]
    try:
        ec2_client.release_address(AllocationId=rid)
        print(f"  RELEASED EIP {rid} ({z.get('public_ip')})")
        return _record(z, "release", "done", detail=f"public_ip={z.get('public_ip')}",
                       rollback="allocate a new EIP (original address not guaranteed)")
    except ClientError as e:
        print(f"  ERROR releasing EIP {rid}: {e}")
        return _record(z, "release", "error", detail=str(e))


def _decommission_ec2(ec2_client, z, opts):
    """Race-check, then stop (reversible) or image+terminate one instance."""
    rid = z["resource_id"]
    try:
        inst = ec2_client.describe_instances(
            InstanceIds=[rid])["Reservations"][0]["Instances"][0]
        state = inst["State"]["Name"]
    except (ClientError, IndexError, KeyError) as e:
        print(f"  SKIP instance {rid}: re-check failed ({e})")
        return _record(z, "skip", "error", detail=str(e))
    if state != "running":
        print(f"  SKIP instance {rid}: no longer running (state={state})")
        return _record(z, "skip", "skipped", detail=f"state={state}")

    if not opts["terminate_ec2"]:
        try:
            ec2_client.stop_instances(InstanceIds=[rid])
            print(f"  STOPPED instance {rid} (reversible; EBS/EIP still billed)")
            return _record(z, "stop", "done",
                           rollback=f"aws ec2 start-instances --instance-ids {rid}")
        except ClientError as e:
            print(f"  ERROR stopping instance {rid}: {e}")
            return _record(z, "stop", "error", detail=str(e))

    backup_id = None
    if opts["backup"]:
        backup_id = image_instance(ec2_client, rid, opts["wait"], opts["timeout"],
                                   opts["retention"], opts["actor"])
        if backup_id is None:
            return _record(z, "image+terminate", "aborted",
                           detail="backup failed; instance NOT terminated")
    try:
        ec2_client.terminate_instances(InstanceIds=[rid])
        print(f"  TERMINATED instance {rid}"
              + (f" (AMI {backup_id})" if backup_id else " (no backup)"))
        rollback = f"aws ec2 run-instances --image-id {backup_id} ..." if backup_id else None
        return _record(z, "image+terminate" if backup_id else "terminate", "done",
                       backup_id=backup_id, rollback=rollback)
    except ClientError as e:
        print(f"  ERROR terminating instance {rid}: {e}")
        return _record(z, "terminate", "error", backup_id=backup_id, detail=str(e))


def decommission(ec2_client, resources, *, backup, wait, timeout,
                 retention_days, terminate_ec2, actor):
    """Safeguarded teardown of SAFE-tier resources.

    For each resource: re-verify it is still wasteful (race guard), back it up
    if applicable (aborting the delete if the backup fails), then delete /
    release / stop. Every outcome is captured as a record for the audit log.
    """
    opts = {"backup": backup, "wait": wait, "timeout": timeout,
            "retention": retention_days, "terminate_ec2": terminate_ec2, "actor": actor}
    handlers = {
        "EBS Volume": _decommission_ebs,
        "Elastic IP": _decommission_eip,
        "EC2 Instance": _decommission_ec2,
    }
    records = []
    for z in resources:
        handler = handlers.get(z["resource_type"])
        if handler:
            records.append(handler(ec2_client, z, opts))
    return records


def write_manifest(records, region, actor, path):
    """Write a decommission manifest (audit trail + rollback reference)."""
    manifest = {
        "generated_at": _utcnow().isoformat(),
        "region": region,
        "performed_by": str(actor),
        "summary": {
            "total": len(records),
            "deleted": sum(1 for r in records
                           if r["status"] == "done" and r["action"] != "stop"),
            "stopped": sum(1 for r in records
                           if r["action"] == "stop" and r["status"] == "done"),
            "aborted": sum(1 for r in records if r["status"] == "aborted"),
            "skipped": sum(1 for r in records if r["status"] == "skipped"),
            "errors": sum(1 for r in records if r["status"] == "error"),
            "backups_created": sum(1 for r in records if r["backup_id"]),
        },
        "actions": records,
    }
    try:
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"\nDecommission manifest written to: {path}")
    except OSError as e:
        print(f"\nWARNING: could not write manifest to {path}: {e}")
    return manifest


# ===========================================================================
# CloudTrail investigation & tiering
# ===========================================================================


def _user_from_event(detail):
    """Extract a human-ish actor from a parsed CloudTrailEvent."""
    identity = detail.get("userIdentity", {})
    return (identity.get("userName")
            or identity.get("arn")
            or identity.get("principalId")
            or "Unknown")


def _is_read_only(detail):
    """True if the event is a read-only API call (readOnly may be bool or str)."""
    return str(detail.get("readOnly", "")).lower() == "true"


class Investigator:
    """Investigates resources in CloudTrail and assigns lifecycle tiers.

    Holds policy thresholds and shared state so a single CloudTrail
    authorization failure disables further lookups (avoiding per-resource
    spam and needless throttling) instead of being retried for every resource.
    """

    def __init__(self, cloudtrail_client, flag_days, escalate_days,
                 decommission_days, lookback_days=CLOUDTRAIL_LOOKBACK_DAYS,
                 exempt_tag=DEFAULT_EXEMPT_TAG, treat_no_activity_as_idle=False,
                 skip_cloudtrail=False):
        self.ct = cloudtrail_client
        self.flag_days = flag_days
        self.escalate_days = escalate_days
        self.decommission_days = decommission_days
        # CloudTrail can't see past its retention window; clamp accordingly.
        self.lookback_days = min(lookback_days, CLOUDTRAIL_LOOKBACK_DAYS)
        self.exempt_tag = exempt_tag
        self.treat_no_activity_as_idle = treat_no_activity_as_idle
        self.skip_cloudtrail = skip_cloudtrail
        self.disabled_reason = None  # set on a global auth failure

    def _tier_for_days(self, days_since):
        if days_since < self.flag_days:
            return TIER_ACTIVE
        if days_since < self.escalate_days:
            return TIER_FLAG
        if days_since < self.decommission_days:
            return TIER_ESCALATE
        return TIER_SAFE

    def _lookup_keys(self, zombie):
        """Identifiers to search CloudTrail under (EIPs: alloc ID + public IP)."""
        keys = [zombie["resource_id"]]
        pub = zombie.get("public_ip")
        if pub and pub not in ("N/A", None):
            keys.append(pub)
        return keys

    def _latest_mutating_event(self, resource_key):
        """Most recent NON-read-only event for a key, or None.

        Filtering out read-only events means a monitoring tool's Describe*
        polling can't make a resource look perpetually ACTIVE, and the result
        no longer depends on whether the account logs read events. Raises
        ClientError to the caller.
        """
        end_time = _utcnow()
        start_time = end_time - datetime.timedelta(days=self.lookback_days)
        response = self.ct.lookup_events(
            LookupAttributes=[
                {"AttributeKey": "ResourceName", "AttributeValue": resource_key}
            ],
            StartTime=start_time,
            EndTime=end_time,
            MaxResults=50,  # events are returned most-recent first
        )
        for event in response.get("Events", []):
            try:
                detail = json.loads(event.get("CloudTrailEvent", "{}"))
            except (ValueError, TypeError):
                detail = {}
                # Unparseable -> treat as activity (protective).
            if detail and _is_read_only(detail):
                continue
            return (event["EventTime"],
                    event.get("EventName", "Unknown"),
                    _user_from_event(detail))
        return None

    def classify(self, zombie):
        """Return the tier decision dict for one resource."""
        # 1. Explicit exemption tag wins over everything.
        tags = zombie.get("tags", {})
        if self.exempt_tag and self.exempt_tag in tags:
            return self._result(TIER_EXEMPT, None, None, None,
                                 f"exempt: tag {self.exempt_tag}={tags[self.exempt_tag]}")

        # 2. Investigation skipped by request.
        if self.skip_cloudtrail:
            return self._result(TIER_NOT_INVESTIGATED, None, None, None,
                                 "CloudTrail investigation skipped (--skip-cloudtrail)")

        # 3. CloudTrail globally unavailable (prior auth failure).
        if self.disabled_reason:
            return self._result(TIER_UNVERIFIABLE, None, None, None, self.disabled_reason)

        # 4. Investigate across all lookup keys, keeping the most recent event.
        latest = None
        try:
            for key in self._lookup_keys(zombie):
                event = self._latest_mutating_event(key)
                if event and (latest is None or event[0] > latest[0]):
                    latest = event
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            if code in ("AccessDenied", "AccessDeniedException",
                        "UnauthorizedOperation"):
                # Disable further lookups: every resource would fail the same way.
                self.disabled_reason = f"CloudTrail access denied ({code})"
                return self._result(TIER_UNVERIFIABLE, None, None, None,
                                    self.disabled_reason)
            return self._result(TIER_UNVERIFIABLE, None, None, None,
                                f"CloudTrail error ({code})")

        # 5. No mutating activity found = absence of evidence, not proof of idle.
        if latest is None:
            reason = (f"no mutating activity in last {self.lookback_days}d "
                      f"(CloudTrail retention limit; ambiguous)")
            if self.treat_no_activity_as_idle:
                return self._result(TIER_SAFE, None, None, None,
                                    reason + " - treated as idle")
            return self._result(TIER_INCONCLUSIVE, None, None, None,
                                reason + " - investigate manually")

        # 6. We found a real event: tier by how long ago it was.
        event_time, event_name, user = latest
        # CloudTrail EventTime is timezone-aware; compare aware-to-aware.
        days_since = max((_utcnow() - event_time).days, 0)  # guard clock skew
        tier = self._tier_for_days(days_since)
        note = " (creation event - provisioned, never used since)" \
            if event_name in CREATION_EVENTS else ""
        return self._result(tier, days_since, event_name, user,
                            f"last mutating activity {days_since}d ago{note}")

    @staticmethod
    def _result(tier, days_since, event, user, reason):
        return {
            "tier": tier,
            "deletable": tier == TIER_SAFE,
            "days_since": days_since,
            "event": event,
            "user": user,
            "reason": reason,
        }


def classify_all(investigator, zombies):
    """Annotate each zombie with its tier under the '_gate' key."""
    print(f"\nInvestigating {len(zombies)} resource(s) in CloudTrail "
          f"(flag >= {investigator.flag_days}d, "
          f"escalate >= {investigator.escalate_days}d, "
          f"decommission >= {investigator.decommission_days}d)...")
    for z in zombies:
        z["_gate"] = investigator.classify(z)
    if investigator.disabled_reason:
        print(f"  NOTE: {investigator.disabled_reason} - remaining resources "
              "marked UNVERIFIABLE (protected). Grant cloudtrail:LookupEvents "
              "for tiering.")
    return zombies


def print_tier_breakdown(zombies):
    """Print resources grouped by lifecycle tier (most-protected first)."""
    print("\n" + "-" * 70)
    print("CLOUDTRAIL ACTIVITY TIERS")
    print("-" * 70)
    for tier in TIER_ORDER:
        items = [z for z in zombies if z["_gate"]["tier"] == tier]
        if not items:
            continue
        print(f"\n  {TIER_LABEL[tier]}  [{len(items)}]")
        for z in items:
            g = z["_gate"]
            print(f"    {z['resource_id']} ({z['resource_type']}) - {g['reason']}")
            if g.get("event"):
                print(f"        last: {g['event']} by {g['user']}")
    print("-" * 70)


# ===========================================================================
# Reporting
# ===========================================================================


def generate_report(all_zombies, region):
    """Generate summary report of zombie resources, including activity tiers."""
    total_monthly_cost = 0
    tier_breakdown = {}
    resources = []

    for z in all_zombies:
        try:
            total_monthly_cost += float(z["monthly_cost_usd"])
        except (ValueError, TypeError):
            pass

        # Public report entry: lift the internal '_gate' into an 'activity' block.
        entry = {k: v for k, v in z.items() if k != "_gate"}
        gate = z.get("_gate")
        if gate:
            tier_breakdown[gate["tier"]] = tier_breakdown.get(gate["tier"], 0) + 1
            entry["activity"] = {
                "tier": gate["tier"],
                "deletable": gate["deletable"],
                "days_since_activity": gate["days_since"],
                "last_event": gate["event"],
                "last_user": gate["user"],
                "detail": gate["reason"],
            }
        resources.append(entry)

    return {
        "generated_at": _utcnow().isoformat(),
        "region": region,
        "total_zombies_found": len(all_zombies),
        "tier_breakdown": tier_breakdown,
        "estimated_monthly_waste_usd": round(total_monthly_cost, 2),
        "estimated_annual_waste_usd": round(total_monthly_cost * 12, 2),
        "zombie_resources": resources,
    }


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


# ===========================================================================
# CLI
# ===========================================================================


def build_parser():
    parser = argparse.ArgumentParser(
        description="Zombie Hunter - Find and clean up wasteful AWS resources"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect & investigate only. Do NOT delete/stop anything.")
    parser.add_argument("--execute", action="store_true",
                        help="Delete/stop SAFE-tier resources. USE WITH CAUTION.")
    parser.add_argument("--region", default=DEFAULT_REGION,
                        help=f"AWS region to scan (default: {DEFAULT_REGION})")
    parser.add_argument("--output", default=None,
                        help="Save report to a JSON file (e.g., report.json)")

    # --- Tier thresholds ---
    parser.add_argument("--flag-days", type=int, default=DEFAULT_FLAG_DAYS,
                        help=f"Idle days before FLAGGED for review (default: {DEFAULT_FLAG_DAYS}).")
    parser.add_argument("--escalate-days", type=int, default=DEFAULT_ESCALATE_DAYS,
                        help=f"Idle days before ESCALATED as likely idle (default: {DEFAULT_ESCALATE_DAYS}).")
    parser.add_argument("--decommission-days", type=int, default=DEFAULT_DECOMMISSION_DAYS,
                        help=("Idle days before SAFE to decommission. The ONLY tier "
                              f"--execute deletes (default: {DEFAULT_DECOMMISSION_DAYS})."))

    # --- Idle EC2 detection ---
    parser.add_argument("--idle-lookback-days", type=int, default=DEFAULT_IDLE_LOOKBACK_DAYS,
                        help=f"CPU/network lookback window in days (default: {DEFAULT_IDLE_LOOKBACK_DAYS}).")
    parser.add_argument("--network-idle-mb-per-day", type=float,
                        default=DEFAULT_NETWORK_IDLE_MB_PER_DAY,
                        help=("Combined NetworkIn+NetworkOut MB/day below which an "
                              f"instance counts as network-idle (default: {DEFAULT_NETWORK_IDLE_MB_PER_DAY})."))

    # --- Safety / investigation ---
    parser.add_argument("--exempt-tag", default=DEFAULT_EXEMPT_TAG,
                        help=("Resources carrying this tag key are EXEMPT and never "
                              f"deleted (default: {DEFAULT_EXEMPT_TAG}). Set empty to disable."))
    parser.add_argument("--treat-no-activity-as-idle", action="store_true",
                        help=("Treat 'no CloudTrail activity found' as SAFE. Only use "
                              "if you trust your CloudTrail coverage."))
    parser.add_argument("--skip-cloudtrail", action="store_true",
                        help="Skip CloudTrail investigation (state scan only). Dry-run only.")
    parser.add_argument("--terminate-idle-ec2", action="store_true",
                        help="Terminate SAFE idle instances instead of stopping them.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt (for --execute).")

    # --- Backup-before-delete safeguards ---
    parser.add_argument("--no-backup", action="store_true",
                        help=("Skip the snapshot/AMI taken before deletion. NOT recommended "
                              "-- without it, deletions have no restore point."))
    parser.add_argument("--snapshot-wait-timeout", type=int,
                        default=DEFAULT_SNAPSHOT_WAIT_TIMEOUT,
                        help=("Seconds to wait for a backup to complete before aborting the "
                              f"delete (default: {DEFAULT_SNAPSHOT_WAIT_TIMEOUT})."))
    parser.add_argument("--snapshot-retention-days", type=int,
                        default=DEFAULT_SNAPSHOT_RETENTION_DAYS,
                        help=("Retention tag (days) applied to backups for later lifecycle "
                              f"cleanup (default: {DEFAULT_SNAPSHOT_RETENTION_DAYS})."))
    parser.add_argument("--decommission-log", default=None,
                        help="Path for the JSON decommission manifest (default: auto-named).")
    return parser


def validate_args(args):
    """Return an error string if args are invalid, else None."""
    if not args.dry_run and not args.execute:
        return "specify --dry-run or --execute"
    if args.dry_run and args.execute:
        return "use either --dry-run or --execute, not both"
    if not (0 < args.flag_days <= args.escalate_days <= args.decommission_days):
        return (f"thresholds must satisfy 0 < flag-days <= escalate-days <= "
                f"decommission-days (got flag={args.flag_days}, "
                f"escalate={args.escalate_days}, decommission={args.decommission_days})")
    if args.idle_lookback_days <= 0:
        return "idle-lookback-days must be positive"
    if args.network_idle_mb_per_day < 0:
        return "network-idle-mb-per-day must be non-negative"
    if args.skip_cloudtrail and args.execute:
        return ("--skip-cloudtrail cannot be used with --execute: deletion "
                "requires CloudTrail investigation")
    return None


def confirm_deletion(count, assume_yes):
    """Return True if deletion is confirmed. Never blocks on a non-TTY."""
    if assume_yes:
        print(f"\n--yes given: proceeding to delete {count} SAFE resource(s).")
        return True
    if not sys.stdin.isatty():
        print("\nERROR: --execute needs confirmation but stdin is not "
              "interactive. Re-run with --yes to confirm non-interactively.")
        return False
    answer = input(f"\nType 'yes' to confirm deletion of {count} SAFE resource(s): ")
    return answer.strip().lower() == "yes"


def main():
    args = build_parser().parse_args()

    err = validate_args(args)
    if err:
        print(f"ERROR: {err}")
        return 2

    print("=" * 60)
    print("THE COST DETECTIVE - Zombie Hunter")
    print(f"   Region: {args.region}")
    print(f"   Mode:   {'DRY RUN (no deletions)' if args.dry_run else 'WARNING: EXECUTE (will delete/stop SAFE resources)'}")
    print("=" * 60)

    clients = get_clients(args.region)
    ec2 = clients["ec2"]
    cw = clients["cloudwatch"]
    ct = clients["cloudtrail"]
    sts = clients["sts"]

    # --- Detect ---
    all_zombies = (
        find_unattached_ebs(ec2)
        + find_unassociated_eips(ec2)
        + find_idle_ec2_instances(ec2, cw, IDLE_CPU_THRESHOLD,
                                  args.network_idle_mb_per_day,
                                  args.idle_lookback_days)
    )

    # --- Investigate (both modes; investigation precedes any decision) ---
    if all_zombies:
        investigator = Investigator(
            ct, args.flag_days, args.escalate_days, args.decommission_days,
            exempt_tag=args.exempt_tag or "",
            treat_no_activity_as_idle=args.treat_no_activity_as_idle,
            skip_cloudtrail=args.skip_cloudtrail,
        )
        classify_all(investigator, all_zombies)

    # --- Report ---
    report = generate_report(all_zombies, args.region)
    print_summary(report)
    if all_zombies:
        print_tier_breakdown(all_zombies)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to: {args.output}")

    # --- Execute (SAFE tier only) ---
    if args.execute and all_zombies:
        print("\nWARNING: EXECUTING CLEANUP...")
        deletable = [z for z in all_zombies if z["_gate"]["deletable"]]
        held = [z for z in all_zombies if not z["_gate"]["deletable"]]

        if held:
            print(f"\n{len(held)} resource(s) below the SAFE tier are held back "
                  "(exempt/active/flagged/escalated/inconclusive/unverifiable).")

        if not deletable:
            print(f"\nNo resources reached the SAFE tier "
                  f"(idle >= {args.decommission_days}d). Nothing to delete.")
            return 0

        backup = not args.no_backup
        print(f"\n{len(deletable)} resource(s) at the SAFE tier, eligible for decommissioning:")
        for z in deletable:
            plan = {
                "EBS Volume": "snapshot then delete" if backup else "delete (no backup)",
                "Elastic IP": "release",
                "EC2 Instance": ("image then terminate" if (args.terminate_idle_ec2 and backup)
                                 else "terminate" if args.terminate_idle_ec2 else "stop (reversible)"),
            }.get(z["resource_type"], "process")
            print(f"  {z['resource_id']} ({z['resource_type']}) -> {plan}")
        if backup:
            print("\nBackups (snapshots/AMIs) will be created and verified before deletion; "
                  "a delete is aborted if its backup fails.")
        else:
            print("\nWARNING: --no-backup set; deletions will have NO restore point.")

        if not confirm_deletion(len(deletable), args.yes):
            print("Cleanup cancelled.")
            return 0

        # Identify the actor for backup tags and the audit manifest.
        try:
            actor = sts.get_caller_identity()["Arn"]
        except ClientError:
            actor = "unknown"

        records = decommission(
            ec2, deletable,
            backup=backup,
            wait=True,  # wait-and-verify: never delete before the backup completes
            timeout=args.snapshot_wait_timeout,
            retention_days=args.snapshot_retention_days,
            terminate_ec2=args.terminate_idle_ec2,
            actor=actor,
        )

        log_path = args.decommission_log or (
            f"decommission-log-{_utcnow().strftime('%Y%m%dT%H%M%SZ')}.json")
        manifest = write_manifest(records, args.region, actor, log_path)

        s = manifest["summary"]
        print("\nDecommission summary: "
              f"{s['deleted']} deleted, {s['stopped']} stopped, "
              f"{s['backups_created']} backups, {s['aborted']} aborted, "
              f"{s['skipped']} skipped, {s['errors']} errors.")
        if not args.terminate_idle_ec2 and any(
                r["action"] == "stop" and r["status"] == "done" for r in records):
            print("NOTE: instances were STOPPED, not terminated - their EBS volumes and "
                  "any EIPs keep billing. Use --terminate-idle-ec2 to remove them.")
        print("\nCleanup complete!")
    elif args.dry_run:
        print("\nDry run complete. No resources were modified.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
