"""
Hello World Lambda handler.

Receives WorkflowRunStateChange events from the OrcaBus EventBridge bus,
validates them via Pydantic, and emits a HelloWorldEvent back to the bus.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import boto3

from hello_world.models import HelloWorldEventDetail, IncomingEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialise the boto3 client outside the handler for connection reuse across invocations
events_client = boto3.client('events')

EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME', 'OrcaBusMain')
EVENT_SOURCE = 'orcabus.helloworld'
EVENT_DETAIL_TYPE = 'HelloWorldEvent'


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entrypoint. Parses the incoming OrcaBus event and emits a HelloWorldEvent.

    Args:
        event: EventBridge event delivered by the event rule
        context: Lambda execution context

    Returns:
        Dict with statusCode and eventId of the emitted event
    """
    logger.info(f'Received event: {json.dumps(event, default=str)}')

    # Parse and validate the incoming event using Pydantic
    incoming = IncomingEvent.model_validate(event)
    wrsc = incoming.detail

    logger.info(
        f'Processing WorkflowRunStateChange: portalRunId={wrsc.portalRunId}, '
        f'workflow={wrsc.workflow.name}, status={wrsc.status}'
    )

    # Build outgoing event detail
    outgoing_detail = HelloWorldEventDetail(
        portalRunId=wrsc.portalRunId,
        workflowName=wrsc.workflow.name,
        status=wrsc.status,
        message=(
            f"Hello World! Processed workflow run '{wrsc.workflowRunName}' "
            f"with status '{wrsc.status}'."
        ),
        processedAt=datetime.now(tz=timezone.utc),
    )

    event_id = emit_event(outgoing_detail)
    logger.info(f'Emitted HelloWorldEvent with id: {event_id}')

    return {'statusCode': 200, 'eventId': event_id}


def emit_event(detail: HelloWorldEventDetail) -> str:
    """
    Put a HelloWorldEvent onto the OrcaBus event bus.

    Args:
        detail: The event payload

    Returns:
        EventId from the EventBridge response

    Raises:
        RuntimeError: When EventBridge rejects the event
    """
    response = events_client.put_events(
        Entries=[
            {
                'Source': EVENT_SOURCE,
                'DetailType': EVENT_DETAIL_TYPE,
                'Detail': detail.model_dump_json(),
                'EventBusName': EVENT_BUS_NAME,
            }
        ]
    )

    if response['FailedEntryCount'] > 0:
        errors = [
            e.get('ErrorMessage', 'unknown') for e in response['Entries'] if 'ErrorCode' in e
        ]
        raise RuntimeError(f'EventBridge put_events failed: {errors}')

    return response['Entries'][0]['EventId']
