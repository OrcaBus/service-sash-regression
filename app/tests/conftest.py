import pytest


@pytest.fixture
def sample_wrsc_event():
    """A complete EventBridge WorkflowRunStateChange event as delivered by the rule."""
    return {
        'id': 'abc-123',
        'source': 'orcabus.workflowmanager',
        'detail-type': 'WorkflowRunStateChange',
        'time': '2026-03-12T00:00:00Z',
        'account': '123456789012',
        'region': 'ap-southeast-2',
        'detail': {
            'id': 'wfr-001',
            'version': '1.0.0',
            'timestamp': '2026-03-12T00:00:00Z',
            'orcabusId': 'orcabus.workflowrun.001',
            'portalRunId': '20260312abcd1234',  # pragma: allowlist secret
            'workflowRunName': 'hello-world-test-run',
            'workflow': {
                'orcabusId': 'orcabus.workflow.001',
                'name': 'hello-world',
                'version': '1.0.0',
            },
            'status': 'SUCCEEDED',
        },
    }
