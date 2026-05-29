#!/usr/bin/env python3
"""
cloudtrail_tracker.py — CloudTrail Last Activity Tracker

Queries CloudTrail to find the last API activity for resources.
Helps determine if a resource is truly unused.

Usage:
  python cloudtrail_tracker.py --resource-id vol-1234567890abcdef --days 90
  python cloudtrail_tracker.py --resource-id i-1234567890abcdef --region eu-west-1
"""

import boto3
import argparse
import json
from datetime import datetime, timedelta
from botocore.exceptions import ClientError


def get_last_activity(cloudtrail_client, resource_id, lookback_days=90):
    """Query CloudTrail for the last API call involving a specific resource."""
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=lookback_days)
    
    print(f"\n🔍 Searching CloudTrail for activity on {resource_id}")
    print(f"   Lookback period: {lookback_days} days ({start_time.date()} to {end_time.date()})")
    
    try:
        paginator = cloudtrail_client.get_paginator('lookup_events')
        
        page_iterator = paginator.paginate(
            LookupAttributes=[
                {
                    'AttributeKey': 'ResourceName',
                    'AttributeValue': resource_id
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            MaxResults=50
        )
        
        events = []
        for page in page_iterator:
            events.extend(page.get('Events', []))
        
        if not events:
            print(f"   ⚠️  No CloudTrail events found for {resource_id}")
            return {
                'resource_id': resource_id,
                'last_activity_time': None,
                'event_name': None,
                'user_identity': None,
                'days_since_activity': lookback_days,
                'status': 'NO_ACTIVITY_FOUND',
                'confidence': 'HIGH_ZOMBIE_PROBABILITY'
            }
        
        latest_event = events[0]
        event_time = latest_event['EventTime']
        event_name = latest_event['EventName']
        
        cloud_trail_event = json.loads(latest_event.get('CloudTrailEvent', '{}'))
        user_identity = cloud_trail_event.get('userIdentity', {})
        user_type = user_identity.get('type', 'Unknown')
        user_name = user_identity.get('userName') or user_identity.get('principalId', 'Unknown')
        
        days_since = (datetime.utcnow() - event_time.replace(tzinfo=None)).days
        
        print(f"   ✅ Last activity found:")
        print(f"      Event: {event_name}")
        print(f"      Time: {event_time}")
        print(f"      User: {user_name} ({user_type})")
        print(f"      Days ago: {days_since}")
        
        confidence = determine_zombie_confidence(event_name, days_since)
        
        return {
            'resource_id': resource_id,
            'last_activity_time': event_time.isoformat(),
            'event_name': event_name,
            'user_identity': f"{user_name} ({user_type})",
            'days_since_activity': days_since,
            'status': 'ACTIVITY_FOUND',
            'confidence': confidence,
            'total_events_found': len(events)
        }
        
    except ClientError as e:
        print(f"   ❌ Error querying CloudTrail: {e}")
        return {
            'resource_id': resource_id,
            'error': str(e),
            'status': 'ERROR'
        }


def determine_zombie_confidence(event_name, days_since):
    """Determine zombie confidence based on last activity type and age."""
    creation_events = ['CreateVolume', 'RunInstances', 'AllocateAddress', 'CreateDBInstance']
    deletion_events = ['DeleteVolume', 'TerminateInstances', 'ReleaseAddress', 'DeleteDBInstance']
    usage_events = ['AttachVolume', 'AssociateAddress', 'StartInstances', 'StopInstances']
    
    if event_name in deletion_events:
        return 'ALREADY_DELETED'
    
    if event_name in creation_events and days_since > 30:
        return 'HIGH_ZOMBIE_PROBABILITY'
    
    if event_name in usage_events and days_since < 7:
        return 'ACTIVE_RESOURCE'
    
    if days_since > 60:
        return 'HIGH_ZOMBIE_PROBABILITY'
    elif days_since > 30:
        return 'MEDIUM_ZOMBIE_PROBABILITY'
    elif days_since > 7:
        return 'LOW_ZOMBIE_PROBABILITY'
    else:
        return 'ACTIVE_RESOURCE'


def get_resource_type(resource_id):
    """Determine resource type from ID prefix."""
    prefixes = {
        'i-': 'EC2 Instance',
        'vol-': 'EBS Volume',
        'eipalloc-': 'Elastic IP',
        'snap-': 'EBS Snapshot',
        'ami-': 'AMI',
        'sg-': 'Security Group',
        'rtb-': 'Route Table',
        'igw-': 'Internet Gateway',
        'nat-': 'NAT Gateway',
        'eni-': 'Network Interface',
        'subnet-': 'Subnet',
        'vpc-': 'VPC'
    }
    
    for prefix, resource_type in prefixes.items():
        if resource_id.startswith(prefix):
            return resource_type
    
    return 'Unknown'


def main():
    parser = argparse.ArgumentParser(
        description="🕵️ CloudTrail Activity Tracker — Find last API activity for resources"
    )
    parser.add_argument(
        '--resource-id',
        required=True,
        help='AWS resource ID to track (e.g., vol-1234567890abcdef, i-1234567890abcdef)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=90,
        help='Number of days to look back in CloudTrail (default: 90)'
    )
    parser.add_argument(
        '--region',
        default='eu-west-1',
        help='AWS region (default: eu-west-1)'
    )
    parser.add_argument(
        '--output',
        help='Save results to JSON file'
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🕵️  CLOUDTRAIL ACTIVITY TRACKER")
    print(f"   Resource: {args.resource_id}")
    print(f"   Type: {get_resource_type(args.resource_id)}")
    print(f"   Region: {args.region}")
    print("=" * 70)
    
    # Create CloudTrail client
    cloudtrail = boto3.client('cloudtrail', region_name=args.region)
    
    # Get last activity
    result = get_last_activity(cloudtrail, args.resource_id, args.days)
    
    # Print summary
    print("\n" + "=" * 70)
    print("📊 ACTIVITY SUMMARY")
    print("=" * 70)
    print(f"  Resource ID:        {result['resource_id']}")
    print(f"  Status:             {result.get('status', 'UNKNOWN')}")
    print(f"  Zombie Confidence:  {result.get('confidence', 'UNKNOWN')}")
    
    if result.get('last_activity_time'):
        print(f"  Last Activity:      {result['last_activity_time']}")
        print(f"  Event Name:         {result['event_name']}")
        print(f"  User:               {result['user_identity']}")
        print(f"  Days Since:         {result['days_since_activity']}")
    else:
        print(f"  Last Activity:      None found in last {args.days} days")
    
    print("=" * 70)
    
    # Recommendation
    print("\n💡 RECOMMENDATION:")
    confidence = result.get('confidence', 'UNKNOWN')
    
    if confidence == 'HIGH_ZOMBIE_PROBABILITY':
        print("   ⚠️  HIGH risk of being a zombie resource")
        print("   → Review and consider deletion")
    elif confidence == 'MEDIUM_ZOMBIE_PROBABILITY':
        print("   ⚠️  MEDIUM risk of being a zombie resource")
        print("   → Investigate usage patterns")
    elif confidence == 'LOW_ZOMBIE_PROBABILITY':
        print("   ✅ LOW risk - resource has recent activity")
    elif confidence == 'ACTIVE_RESOURCE':
        print("   ✅ ACTIVE resource - do not delete")
    elif confidence == 'ALREADY_DELETED':
        print("   ℹ️  Resource was already deleted")
    
    # Save to file if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Results saved to: {args.output}")


if __name__ == '__main__':
    main()
