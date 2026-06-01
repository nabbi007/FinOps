#!/usr/bin/env python3
"""
zombie_hunter_enhanced.py — Enhanced Zombie Detector with CloudTrail Integration
=================================================================================
Improved version that uses CloudTrail to determine last activity and
provides confidence scores for zombie classification.

Features:
  - CloudTrail last activity tracking
  - Multi-criteria zombie scoring
  - Detailed audit reports with activity history
  - Configurable confidence thresholds

Usage:
  python zombie_hunter_enhanced.py --dry-run --with-cloudtrail
  python zombie_hunter_enhanced.py --dry-run --min-confidence HIGH
"""

import sys
import boto3
import argparse
import json
import datetime
from datetime import timedelta
from botocore.exceptions import ClientError


# Windows consoles often default to cp1252, which cannot encode the emoji this
# script prints, raising UnicodeEncodeError mid-run. Force UTF-8 so output never
# crashes regardless of the host console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# Configuration
DEFAULT_REGION = "eu-west-1"
IDLE_CPU_THRESHOLD = 5.0
IDLE_LOOKBACK_DAYS = 14
CLOUDTRAIL_LOOKBACK_DAYS = 90


def get_clients(region):
    """Initialize AWS service clients."""
    return {
        "ec2": boto3.client("ec2", region_name=region),
        "cloudwatch": boto3.client("cloudwatch", region_name=region),
        "cloudtrail": boto3.client("cloudtrail", region_name=region),
    }


def get_cloudtrail_last_activity(cloudtrail_client, resource_id):
    """
    Query CloudTrail for last activity on a resource.
    Returns days since last activity and event details.
    """
    try:
        end_time = datetime.datetime.utcnow()
        start_time = end_time - timedelta(days=CLOUDTRAIL_LOOKBACK_DAYS)
        
        response = cloudtrail_client.lookup_events(
            LookupAttributes=[
                {
                    'AttributeKey': 'ResourceName',
                    'AttributeValue': resource_id
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            MaxResults=1
        )
        
        events = response.get('Events', [])
        if not events:
            return {
                'days_since_activity': CLOUDTRAIL_LOOKBACK_DAYS,
                'last_event': None,
                'last_event_time': None,
                'user': None
            }
        
        latest_event = events[0]
        event_time = latest_event['EventTime']
        days_since = (datetime.datetime.utcnow() - event_time.replace(tzinfo=None)).days
        
        cloud_trail_event = json.loads(latest_event.get('CloudTrailEvent', '{}'))
        user_identity = cloud_trail_event.get('userIdentity', {})
        user_name = user_identity.get('userName') or user_identity.get('principalId', 'Unknown')
        
        return {
            'days_since_activity': days_since,
            'last_event': latest_event['EventName'],
            'last_event_time': event_time.isoformat(),
            'user': user_name
        }
        
    except ClientError as e:
        print(f"  ⚠️  CloudTrail query failed for {resource_id}: {e}")
        return None


def calculate_zombie_score(criteria):
    """
    Calculate zombie confidence score based on multiple criteria.
    
    Scoring system:
    - State-based: 100 points (unattached/unassociated)
    - Metrics-based: 0-100 points (CPU, network, connections)
    - Activity-based: 0-100 points (CloudTrail last activity)
    - Age-based: 0-50 points (how long it's existed)
    
    Returns: score (0-100) and confidence level (LOW/MEDIUM/HIGH/CRITICAL)
    """
    score = 0
    max_score = 0
    
    # State-based scoring (definitive)
    if 'state_score' in criteria:
        score += criteria['state_score']
        max_score += 100
    
    # Metrics-based scoring
    if 'metrics_score' in criteria:
        score += criteria['metrics_score']
        max_score += 100
    
    # Activity-based scoring (CloudTrail)
    if 'activity_score' in criteria:
        score += criteria['activity_score']
        max_score += 100
    
    # Age-based scoring
    if 'age_score' in criteria:
        score += criteria['age_score']
        max_score += 50
    
    # Normalize to 0-100
    if max_score > 0:
        normalized_score = (score / max_score) * 100
    else:
        normalized_score = 0
    
    # Determine confidence level
    if normalized_score >= 90:
        confidence = "CRITICAL"
    elif normalized_score >= 70:
        confidence = "HIGH"
    elif normalized_score >= 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    
    return round(normalized_score, 1), confidence


def find_unattached_ebs_enhanced(ec2_client, cloudtrail_client, use_cloudtrail):
    """Enhanced EBS volume detection with CloudTrail integration."""
    print("\n🔍 Scanning for unattached EBS volumes...")
    
    response = ec2_client.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}]
    )
    
    zombies = []
    for volume in response["Volumes"]:
        vol_id = volume["VolumeId"]
        size_gb = volume["Size"]
        vol_type = volume["VolumeType"]
        created = volume["CreateTime"]
        age_days = (datetime.datetime.utcnow() - created.replace(tzinfo=None)).days
        tags = {t["Key"]: t["Value"] for t in volume.get("Tags", [])}
        
        # Calculate cost
        cost_per_gb = 0.08 if vol_type == "gp3" else 0.10
        monthly_cost = size_gb * cost_per_gb
        
        # Scoring criteria
        criteria = {
            'state_score': 100,  # Unattached = definite waste
            'age_score': min(age_days / 30 * 50, 50)  # Older = more likely zombie
        }
        
        # CloudTrail activity check
        cloudtrail_data = None
        if use_cloudtrail:
            cloudtrail_data = get_cloudtrail_last_activity(cloudtrail_client, vol_id)
            if cloudtrail_data:
                days_inactive = cloudtrail_data['days_since_activity']
                # More days inactive = higher score
                criteria['activity_score'] = min(days_inactive / 90 * 100, 100)
        
        zombie_score, confidence = calculate_zombie_score(criteria)
        
        zombie_entry = {
            "resource_type": "EBS Volume",
            "resource_id": vol_id,
            "details": f"{size_gb} GB {vol_type}",
            "created": created.strftime("%Y-%m-%d"),
            "age_days": age_days,
            "monthly_cost_usd": round(monthly_cost, 2),
            "tags": tags,
            "zombie_score": zombie_score,
            "confidence": confidence,
            "criteria": criteria
        }
        
        if cloudtrail_data:
            zombie_entry["cloudtrail"] = cloudtrail_data
        
        zombies.append(zombie_entry)
        
        # Print with confidence indicator
        confidence_emoji = {
            "CRITICAL": "🔴",
            "HIGH": "🟠",
            "MEDIUM": "🟡",
            "LOW": "🟢"
        }
        
        print(f"  {confidence_emoji.get(confidence, '⚠️')} {vol_id} | {size_gb}GB {vol_type} | "
              f"Age: {age_days}d | Score: {zombie_score} | Confidence: {confidence} | "
              f"~${monthly_cost:.2f}/mo")
        
        if cloudtrail_data and cloudtrail_data['last_event']:
            print(f"     └─ Last activity: {cloudtrail_data['last_event']} "
                  f"({cloudtrail_data['days_since_activity']} days ago)")
    
    if not zombies:
        print("  ✅ No unattached EBS volumes found.")
    
    return zombies


def find_unassociated_eips_enhanced(ec2_client, cloudtrail_client, use_cloudtrail):
    """Enhanced Elastic IP detection with CloudTrail integration."""
    print("\n🔍 Scanning for unassociated Elastic IPs...")
    
    response = ec2_client.describe_addresses()
    zombies = []
    
    for addr in response["Addresses"]:
        if "AssociationId" not in addr:
            eip = addr.get("PublicIp", "N/A")
            alloc_id = addr.get("AllocationId", "N/A")
            tags = {t["Key"]: t["Value"] for t in addr.get("Tags", [])}
            
            # Scoring criteria
            criteria = {
                'state_score': 100,  # Unassociated = definite waste
            }
            
            # CloudTrail activity check
            cloudtrail_data = None
            if use_cloudtrail:
                cloudtrail_data = get_cloudtrail_last_activity(cloudtrail_client, alloc_id)
                if cloudtrail_data:
                    days_inactive = cloudtrail_data['days_since_activity']
                    criteria['activity_score'] = min(days_inactive / 90 * 100, 100)
            
            zombie_score, confidence = calculate_zombie_score(criteria)
            
            zombie_entry = {
                "resource_type": "Elastic IP",
                "resource_id": alloc_id,
                "details": f"Public IP: {eip}",
                "monthly_cost_usd": 3.65,
                "tags": tags,
                "zombie_score": zombie_score,
                "confidence": confidence,
                "criteria": criteria
            }
            
            if cloudtrail_data:
                zombie_entry["cloudtrail"] = cloudtrail_data
            
            zombies.append(zombie_entry)
            
            confidence_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
            print(f"  {confidence_emoji.get(confidence, '⚠️')} {eip} | {alloc_id} | "
                  f"Score: {zombie_score} | Confidence: {confidence} | ~$3.65/mo")
            
            if cloudtrail_data and cloudtrail_data['last_event']:
                print(f"     └─ Last activity: {cloudtrail_data['last_event']} "
                      f"({cloudtrail_data['days_since_activity']} days ago)")
    
    if not zombies:
        print("  ✅ No unassociated Elastic IPs found.")
    
    return zombies


def get_average_cpu(cw_client, instance_id, days=IDLE_LOOKBACK_DAYS):
    """Get average CPU utilization for an EC2 instance."""
    end_time = datetime.datetime.utcnow()
    start_time = end_time - timedelta(days=days)
    
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
            return 0.0
        avg = sum(d["Average"] for d in datapoints) / len(datapoints)
        return round(avg, 2)
    except ClientError:
        return -1


def find_idle_ec2_instances_enhanced(ec2_client, cw_client, cloudtrail_client, use_cloudtrail):
    """Enhanced EC2 idle detection with CloudTrail integration."""
    print(f"\n🔍 Scanning for idle EC2 instances (avg CPU < {IDLE_CPU_THRESHOLD}% over {IDLE_LOOKBACK_DAYS} days)...")
    
    response = ec2_client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )
    
    zombies = []
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_id = instance["InstanceId"]
            instance_type = instance["InstanceType"]
            launch_time = instance["LaunchTime"]
            age_days = (datetime.datetime.utcnow() - launch_time.replace(tzinfo=None)).days
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            name = tags.get("Name", "Unnamed")
            
            avg_cpu = get_average_cpu(cw_client, instance_id)
            
            if avg_cpu < IDLE_CPU_THRESHOLD:
                # Scoring criteria
                cpu_score = (IDLE_CPU_THRESHOLD - avg_cpu) / IDLE_CPU_THRESHOLD * 100
                criteria = {
                    'metrics_score': cpu_score,
                    'age_score': min(age_days / 30 * 50, 50)
                }
                
                # CloudTrail activity check
                cloudtrail_data = None
                if use_cloudtrail:
                    cloudtrail_data = get_cloudtrail_last_activity(cloudtrail_client, instance_id)
                    if cloudtrail_data:
                        days_inactive = cloudtrail_data['days_since_activity']
                        criteria['activity_score'] = min(days_inactive / 90 * 100, 100)
                
                zombie_score, confidence = calculate_zombie_score(criteria)
                
                zombie_entry = {
                    "resource_type": "EC2 Instance",
                    "resource_id": instance_id,
                    "details": f"{instance_type} | Name: {name} | Avg CPU: {avg_cpu}%",
                    "created": launch_time.strftime("%Y-%m-%d"),
                    "age_days": age_days,
                    "monthly_cost_usd": "varies",
                    "tags": tags,
                    "zombie_score": zombie_score,
                    "confidence": confidence,
                    "criteria": criteria,
                    "avg_cpu": avg_cpu
                }
                
                if cloudtrail_data:
                    zombie_entry["cloudtrail"] = cloudtrail_data
                
                zombies.append(zombie_entry)
                
                confidence_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
                print(f"  {confidence_emoji.get(confidence, '⚠️')} {instance_id} ({instance_type}) | "
                      f"Name: {name} | CPU: {avg_cpu}% | Age: {age_days}d | "
                      f"Score: {zombie_score} | Confidence: {confidence}")
                
                if cloudtrail_data and cloudtrail_data['last_event']:
                    print(f"     └─ Last activity: {cloudtrail_data['last_event']} "
                          f"({cloudtrail_data['days_since_activity']} days ago)")
    
    if not zombies:
        print("  ✅ No idle EC2 instances found.")
    
    return zombies


def generate_enhanced_report(all_zombies, region, use_cloudtrail):
    """Generate enhanced audit report with confidence scores."""
    total_monthly_cost = 0
    confidence_breakdown = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    
    for z in all_zombies:
        try:
            total_monthly_cost += float(z["monthly_cost_usd"])
        except (ValueError, TypeError):
            pass
        
        confidence_breakdown[z.get("confidence", "LOW")] += 1
    
    report = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "region": region,
        "cloudtrail_enabled": use_cloudtrail,
        "total_zombies_found": len(all_zombies),
        "confidence_breakdown": confidence_breakdown,
        "estimated_monthly_waste_usd": round(total_monthly_cost, 2),
        "estimated_annual_waste_usd": round(total_monthly_cost * 12, 2),
        "zombie_resources": all_zombies,
    }
    
    return report


def print_enhanced_summary(report):
    """Print enhanced summary with confidence breakdown."""
    print("\n" + "=" * 70)
    print("📊 ENHANCED ZOMBIE HUNTER REPORT")
    print("=" * 70)
    print(f"  🗓️  Generated:        {report['generated_at']}")
    print(f"  🌍 Region:           {report['region']}")
    print(f"  🔍 CloudTrail:       {'Enabled' if report['cloudtrail_enabled'] else 'Disabled'}")
    print(f"  🧟 Zombies Found:    {report['total_zombies_found']}")
    print(f"  💸 Monthly Waste:    ${report['estimated_monthly_waste_usd']:.2f}")
    print(f"  💸 Annual Waste:     ${report['estimated_annual_waste_usd']:.2f}")
    print("\n  📈 Confidence Breakdown:")
    print(f"     🔴 CRITICAL: {report['confidence_breakdown']['CRITICAL']}")
    print(f"     🟠 HIGH:     {report['confidence_breakdown']['HIGH']}")
    print(f"     🟡 MEDIUM:   {report['confidence_breakdown']['MEDIUM']}")
    print(f"     🟢 LOW:      {report['confidence_breakdown']['LOW']}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="🕵️ Enhanced Zombie Hunter with CloudTrail integration"
    )
    parser.add_argument("--dry-run", action="store_true", help="Detect only, no deletions")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--output", help="Save report to JSON file")
    parser.add_argument("--with-cloudtrail", action="store_true", help="Enable CloudTrail activity tracking")
    parser.add_argument("--min-confidence", choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                       default="LOW", help="Minimum confidence level to report (default: LOW)")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🕵️  ENHANCED COST DETECTIVE — Zombie Hunter")
    print(f"   Region: {args.region}")
    print(f"   CloudTrail: {'Enabled' if args.with_cloudtrail else 'Disabled'}")
    print(f"   Min Confidence: {args.min_confidence}")
    print("=" * 70)
    
    clients = get_clients(args.region)
    ec2 = clients["ec2"]
    cw = clients["cloudwatch"]
    ct = clients["cloudtrail"]
    
    # Detect zombies
    ebs_zombies = find_unattached_ebs_enhanced(ec2, ct, args.with_cloudtrail)
    eip_zombies = find_unassociated_eips_enhanced(ec2, ct, args.with_cloudtrail)
    ec2_zombies = find_idle_ec2_instances_enhanced(ec2, cw, ct, args.with_cloudtrail)
    
    all_zombies = ebs_zombies + eip_zombies + ec2_zombies
    
    # Filter by confidence level
    confidence_order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    min_level = confidence_order.index(args.min_confidence)
    filtered_zombies = [
        z for z in all_zombies
        if confidence_order.index(z.get("confidence", "LOW")) >= min_level
    ]
    
    # Generate and print report
    report = generate_enhanced_report(filtered_zombies, args.region, args.with_cloudtrail)
    print_enhanced_summary(report)
    
    # Save report
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n💾 Report saved to: {args.output}")
    
    print("\n✅ Scan complete!")


if __name__ == "__main__":
    main()
