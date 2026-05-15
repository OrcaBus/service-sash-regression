"""
Tests for the Hello World Lambda handler.

Uses unittest.mock.patch to mock the boto3 EventBridge client
so no AWS credentials or network calls are required.
"""
import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest

# Set env vars before importing the handler module so boto3 client initialises cleanly
os.environ.setdefault('EVENT_BUS_NAME', 'OrcaBusMain')
os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-southeast-2')


class TestHandler:
    """Tests for the Lambda handler function."""

    def test_handler_success(self, sample_wrsc_event):
        """Handler processes a valid WRSC event and returns statusCode 200."""
        mock_response = {
            'FailedEntryCount': 0,
            'Entries': [{'EventId': 'mock-event-id-123'}],
        }
        with patch('hello_world.lambdas.handler.events_client') as mock_client:
            mock_client.put_events.return_value = mock_response

            from hello_world.lambdas.handler import lambda_handler

            result = lambda_handler(sample_wrsc_event, None)

        assert result['statusCode'] == 200
        assert result['eventId'] == 'mock-event-id-123'
        mock_client.put_events.assert_called_once()

    def test_handler_emits_correct_event_metadata(self, sample_wrsc_event):
        """Emitted event has the correct source, detail-type and bus name."""
        mock_response = {
            'FailedEntryCount': 0,
            'Entries': [{'EventId': 'mock-event-id-456'}],
        }
        with patch('hello_world.lambdas.handler.events_client') as mock_client:
            mock_client.put_events.return_value = mock_response

            from hello_world.lambdas.handler import lambda_handler

            lambda_handler(sample_wrsc_event, None)

        entry = mock_client.put_events.call_args.kwargs['Entries'][0]
        assert entry['Source'] == 'orcabus.helloworld'
        assert entry['DetailType'] == 'HelloWorldEvent'
        assert entry['EventBusName'] == 'OrcaBusMain'

    def test_handler_detail_contains_expected_fields(self, sample_wrsc_event):
        """Emitted event detail contains the fields extracted from the incoming event."""
        mock_response = {
            'FailedEntryCount': 0,
            'Entries': [{'EventId': 'mock-event-id-789'}],
        }
        with patch('hello_world.lambdas.handler.events_client') as mock_client:
            mock_client.put_events.return_value = mock_response

            from hello_world.lambdas.handler import lambda_handler

            lambda_handler(sample_wrsc_event, None)

        entry = mock_client.put_events.call_args.kwargs['Entries'][0]
        detail = json.loads(entry['Detail'])
        assert detail['portalRunId'] == '20260312abcd1234'  # pragma: allowlist secret
        assert detail['workflowName'] == 'hello-world'
        assert detail['status'] == 'SUCCEEDED'
        assert 'processedAt' in detail

    def test_handler_raises_on_eventbridge_failure(self, sample_wrsc_event):
        """Handler raises RuntimeError when EventBridge rejects the event."""
        mock_response = {
            'FailedEntryCount': 1,
            'Entries': [{'ErrorCode': 'ThrottlingException', 'ErrorMessage': 'Rate exceeded'}],
        }
        with patch('hello_world.lambdas.handler.events_client') as mock_client:
            mock_client.put_events.return_value = mock_response

            from hello_world.lambdas.handler import lambda_handler

            with pytest.raises(RuntimeError, match='EventBridge put_events failed'):
                lambda_handler(sample_wrsc_event, None)


class TestModels:
    """Tests for Pydantic model validation."""

    def test_incoming_event_parses_hyphenated_detail_type(self, sample_wrsc_event):
        """IncomingEvent parses the hyphenated 'detail-type' key correctly."""
        from hello_world.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail_type == 'WorkflowRunStateChange'

    def test_incoming_event_parses_nested_workflow(self, sample_wrsc_event):
        """WorkflowRunStateChange detail is fully parsed including nested Workflow."""
        from hello_world.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail.workflow.name == 'hello-world'
        assert event.detail.workflow.version == '1.0.0'
        assert event.detail.portalRunId == '20260312abcd1234'  # pragma: allowlist secret

    def test_hello_world_event_detail_serializes_to_json(self):
        """HelloWorldEventDetail serializes to valid JSON with expected fields."""
        from datetime import timezone

        from hello_world.models import HelloWorldEventDetail

        detail = HelloWorldEventDetail(
            portalRunId='20260312abcd1234',  # pragma: allowlist secret
            workflowName='hello-world',
            status='SUCCEEDED',
            message='Hello World!',
            processedAt=datetime.now(tz=timezone.utc),
        )
        serialized = detail.model_dump_json()
        parsed = json.loads(serialized)
        assert parsed['portalRunId'] == '20260312abcd1234'  # pragma: allowlist secret
        assert parsed['workflowName'] == 'hello-world'
