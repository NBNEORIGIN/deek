"""
SP-API Notifications — SQS integration for real-time listing change events.

Architecture:
  Amazon SP-API → SQS Queue (per region) → Cairn long-poll processor

Notification types:
  LISTINGS_ITEM_STATUS_CHANGE — listing active/inactive/suppressed
  LISTINGS_ITEM_ISSUES_CHANGE — quality/compliance issues
  LISTINGS_ITEM_MFN_QUANTITY_CHANGE — MFN inventory quantity

Setup: see docs/cairn/amazon_notifications_setup.md
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Literal

import boto3
from botocore.exceptions import ClientError

from .client import (Region, spapi_get, spapi_post, SELLER_IDS,
                     spapi_get_grantless, spapi_post_grantless, spapi_delete_grantless)
from core.amazon_intel.db import get_conn

logger = logging.getLogger(__name__)

# SQS queue URLs per region
SQS_QUEUE_URLS: dict[str, str] = {
    'EU': os.getenv('AWS_SQS_QUEUE_URL_EU', ''),
    'NA': os.getenv('AWS_SQS_QUEUE_URL_NA', ''),
    'FE': os.getenv('AWS_SQS_QUEUE_URL_FE', ''),
}

# SQS regions for boto3 client
SQS_REGIONS: dict[str, str] = {
    'EU': 'eu-west-2',
    'NA': 'us-east-1',
    'FE': 'ap-southeast-2',
}

# IAM role ARN for Amazon to publish to our queues
SQS_ROLE_ARN = os.getenv('AWS_SPAPI_SQS_ROLE_ARN', '')

NOTIFICATION_TYPES = [
    'LISTINGS_ITEM_STATUS_CHANGE',
    'LISTINGS_ITEM_ISSUES_CHANGE',
    'LISTINGS_ITEM_MFN_QUANTITY_CHANGE',
]


def _sqs_client(region: Region):
    """Create a boto3 SQS client for the given SP-API region."""
    aws_region = SQS_REGIONS[region]
    return boto3.client(
        'sqs',
        region_name=aws_region,
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    )


# ── SP-API Destination + Subscription Management ────────────────────────────

def create_destination(region: Region, queue_url: str | None = None,
                       role_arn: str | None = None) -> dict:
    """
    Create an SQS destination in SP-API for this region.
    Returns the destination ID.
    """
    url = queue_url or SQS_QUEUE_URLS.get(region, '')
    arn = role_arn or SQS_ROLE_ARN
    if not url or not arn:
        raise ValueError(f"Missing SQS queue URL or role ARN for region {region}")

    # Extract ARN from queue URL
    # https://sqs.eu-west-2.amazonaws.com/123456789/queue-name
    parts = url.replace('https://', '').split('/')
    account_id = parts[1] if len(parts) >= 3 else ''
    queue_name = parts[2] if len(parts) >= 3 else ''
    aws_region = SQS_REGIONS[region]
    queue_arn = f"arn:aws:sqs:{aws_region}:{account_id}:{queue_name}"

    body = {
        'name': f'cairn-spapi-{region.lower()}',
        'resourceSpecification': {
            'sqs': {
                'arn': queue_arn,
            },
        },
    }

    # Destinations use grantless auth
    result = spapi_post_grantless(region, '/notifications/v1/destinations', body)
    return result.get('payload', result)


def list_destinations(region: Region) -> list[dict]:
    """List all notification destinations for this region."""
    result = spapi_get_grantless(region, '/notifications/v1/destinations')
    return result.get('payload', [])


def delete_destination(region: Region, destination_id: str) -> dict:
    """Delete a notification destination."""
    spapi_delete_grantless(region, f'/notifications/v1/destinations/{destination_id}')
    return {'deleted': destination_id}


def create_subscription(region: Region, notification_type: str,
                        destination_id: str) -> dict:
    """
    Subscribe to a notification type, routing events to the given destination.
    """
    body = {
        'payloadVersion': '1.0',
        'destinationId': destination_id,
    }
    path = f'/notifications/v1/subscriptions/{notification_type}'
    result = spapi_post(region, path, body)
    return result.get('payload', result)


def get_subscription(region: Region, notification_type: str) -> dict | None:
    """Get current subscription for a notification type."""
    path = f'/notifications/v1/subscriptions/{notification_type}'
    try:
        result = spapi_get(region, path)
        return result.get('payload', result)
    except Exception:
        return None


def setup_notifications(region: Region) -> dict:
    """
    Full setup: create destination + subscribe to all listing notification types.
    Idempotent — skips if already subscribed.
    """
    results = {'region': region, 'destination': None, 'subscriptions': {}}

    # Check existing destinations
    existing_dests = list_destinations(region)
    dest_id = None
    for d in existing_dests:
        if d.get('name', '').startswith('cairn-spapi'):
            dest_id = d.get('destinationId')
            results['destination'] = {'id': dest_id, 'status': 'existing'}
            break

    if not dest_id:
        dest = create_destination(region)
        dest_id = dest.get('destinationId')
        results['destination'] = {'id': dest_id, 'status': 'created'}

    # Subscribe to each notification type
    for nt in NOTIFICATION_TYPES:
        existing = get_subscription(region, nt)
        if existing and existing.get('subscriptionId'):
            results['subscriptions'][nt] = {
                'id': existing['subscriptionId'],
                'status': 'existing',
            }
        else:
            try:
                sub = create_subscription(region, nt, dest_id)
                results['subscriptions'][nt] = {
                    'id': sub.get('subscriptionId'),
                    'status': 'created',
                }
            except Exception as e:
                results['subscriptions'][nt] = {
                    'status': 'error',
                    'error': str(e)[:200],
                }

    return results


# ── SQS Message Processing ──────────────────────────────────────────────────

def poll_notifications(region: Region, max_messages: int = 10,
                       wait_time: int = 20) -> list[dict]:
    """
    Long-poll SQS for notification messages.
    Returns parsed notification payloads.
    Deletes messages after successful receipt.
    """
    queue_url = SQS_QUEUE_URLS.get(region, '')
    if not queue_url:
        return []

    client = _sqs_client(region)
    try:
        resp = client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time,
            AttributeNames=['All'],
            MessageAttributeNames=['All'],
        )
    except ClientError as e:
        logger.error("SQS receive error (region=%s): %s", region, str(e)[:200])
        return []

    messages = resp.get('Messages', [])
    notifications = []

    for msg in messages:
        receipt = msg.get('ReceiptHandle', '')
        body = msg.get('Body', '{}')

        try:
            payload = json.loads(body)
            notification = _parse_notification(payload, region)
            notifications.append(notification)
            _store_notification(notification)

            # Delete after successful processing
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        except Exception as e:
            logger.error("Failed to process SQS message: %s", str(e)[:200])

    return notifications


def _parse_notification(payload: dict, region: str) -> dict:
    """Parse an SP-API notification payload into a standard format."""
    notification_type = payload.get('notificationType', 'UNKNOWN')
    event_time = payload.get('eventTime', '')
    notification_payload = payload.get('payload', {})

    # Extract ASIN/SKU from different notification types
    asin = ''
    sku = ''
    seller_id = ''

    if notification_type == 'LISTINGS_ITEM_STATUS_CHANGE':
        item = notification_payload.get('listings_item_status_change', {})
        asin = item.get('asin', '')
        sku = item.get('seller_sku', '')
        seller_id = item.get('seller_id', '')
    elif notification_type == 'LISTINGS_ITEM_ISSUES_CHANGE':
        item = notification_payload.get('listings_item_issues_change', {})
        asin = item.get('asin', '')
        sku = item.get('seller_sku', '')
        seller_id = item.get('seller_id', '')
    elif notification_type == 'LISTINGS_ITEM_MFN_QUANTITY_CHANGE':
        item = notification_payload.get('listings_item_mfn_quantity_change', {})
        asin = item.get('asin', '')
        sku = item.get('seller_sku', '')
        seller_id = item.get('seller_id', '')

    return {
        'notification_type': notification_type,
        'region': region,
        'asin': asin,
        'sku': sku,
        'seller_id': seller_id,
        'event_time': event_time,
        'payload': notification_payload,
        'raw': payload,
        'received_at': datetime.now(timezone.utc).isoformat(),
    }


def _store_notification(notification: dict):
    """Store a notification event in the database for audit + processing."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ami_notification_events
                    (notification_type, region, asin, sku, seller_id,
                     event_time, payload, received_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                notification['notification_type'],
                notification['region'],
                notification['asin'],
                notification['sku'],
                notification['seller_id'],
                notification['event_time'] or None,
                json.dumps(notification['payload']),
            ))
            conn.commit()


def run_notification_processor(regions: list[str] | None = None,
                                poll_cycles: int = 1) -> dict:
    """
    Run the notification processor — polls SQS for all active regions.
    In production, this runs as a background task or separate process.
    """
    target_regions = regions or [r for r in SQS_QUEUE_URLS if SQS_QUEUE_URLS[r]]
    results = {}

    for region in target_regions:
        region_notifications = []
        for _ in range(poll_cycles):
            msgs = poll_notifications(region, max_messages=10, wait_time=5)
            region_notifications.extend(msgs)
            if not msgs:
                break

        results[region] = {
            'notifications_received': len(region_notifications),
            'types': {},
        }
        for n in region_notifications:
            nt = n['notification_type']
            results[region]['types'][nt] = results[region]['types'].get(nt, 0) + 1

    return results


def send_test_notification(region: Region, asin: str = 'B000TEST01',
                           notification_type: str = 'LISTINGS_ITEM_STATUS_CHANGE') -> dict:
    """
    Send a test notification to the SQS queue for verification.
    This simulates what Amazon would send.
    """
    queue_url = SQS_QUEUE_URLS.get(region, '')
    if not queue_url:
        raise ValueError(f"No SQS queue URL for region {region}")

    seller_id = SELLER_IDS.get(region, '')
    test_payload = {
        'notificationType': notification_type,
        'eventTime': datetime.now(timezone.utc).isoformat(),
        'payload': {
            'listings_item_status_change': {
                'seller_id': seller_id,
                'marketplace_id': 'TEST',
                'asin': asin,
                'seller_sku': 'TEST-SKU-001',
                'status': ['DISCOVERABLE'],
                'created_date': datetime.now(timezone.utc).isoformat(),
            },
        },
    }

    client = _sqs_client(region)
    resp = client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(test_payload),
    )

    return {
        'message_id': resp.get('MessageId', ''),
        'region': region,
        'queue_url': queue_url,
        'test_asin': asin,
    }
