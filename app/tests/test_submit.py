"""Unit tests for submitter/submit.py — mocks all AWS/HTTP calls."""

import json
import re
import pytest
from unittest.mock import patch, MagicMock

ENV = {
    "ORCABUS_TOKEN_SECRET_ID": "orcabus/token",
    "HOSTNAME_SSM_PARAMETER_NAME": "/hosted_zone/umccr/name",
    "WRU_VALIDATOR_LAMBDA_NAME": "WruDraftValidator",
    "EVENTS_BUS_NAME": "OrcaBusMain",
}

MOCK_WORKFLOW = {
    "orcabusId": "wfl.abc123",
    "name": "sash",
    "version": "0.7.0",
    "codeVersion": "abc1234",
    "executionEngine": "ICA",
}

MOCK_TUMOR_LIB = {"libraryId": "L2301218", "orcabusId": "lib.tumor111"}
MOCK_NORMAL_LIB = {"libraryId": "L2301217", "orcabusId": "lib.normal222"}

TUMOR_ID = "L2301218"
NORMAL_ID = "L2301217"


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    import submitter.submit as s
    s._token_cache = None
    s._hostname_cache = None


@pytest.fixture()
def mock_boto3():
    with patch("submitter.submit.boto3") as m:
        sm = MagicMock()
        sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"id_token": "fake-token"})
        }
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "prod.umccr.org"}}
        lam = MagicMock()
        lam.invoke.return_value = {
            "Payload": MagicMock(read=lambda: json.dumps({"statusCode": 200}).encode())
        }
        events = MagicMock()

        m.client.side_effect = lambda svc, **kw: {
            "secretsmanager": sm,
            "ssm": ssm,
            "lambda": lam,
            "events": events,
        }[svc]
        yield {"sm": sm, "ssm": ssm, "lambda": lam, "events": events}


@pytest.fixture()
def mock_requests(no_existing_run=True):
    with patch("submitter.submit.requests") as m:
        def get_side_effect(url, params=None, **kw):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "workflowrun" in url:
                resp.json.return_value = {"results": []}  # no existing run by default
            elif "metadata" in url:
                lib_id = (params or {}).get("libraryId", "")
                lib = MOCK_TUMOR_LIB if lib_id == TUMOR_ID else MOCK_NORMAL_LIB
                resp.json.return_value = {"results": [lib]}
            else:  # /api/v1/workflow
                resp.json.return_value = {"results": [MOCK_WORKFLOW]}
            return resp

        m.get.side_effect = get_side_effect
        yield m


# -- Submission path --

def test_portal_run_id_format(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    result = submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    assert re.match(r"^\d{8}[0-9a-f]{8}$", result["portal_run_id"])
    assert result["action"] == "submitted"


def test_workflow_run_name_encodes_both_versions(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    lam = mock_boto3["lambda"]
    payload = json.loads(lam.invoke.call_args[1]["Payload"])
    assert "umccr_tested_sash_0_7_0_vs_0_6_4_" in payload["workflowRunName"]


def test_draft_payload_shape(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    lam = mock_boto3["lambda"]
    payload = json.loads(lam.invoke.call_args[1]["Payload"])
    assert payload["status"] == "DRAFT"
    assert payload["workflow"]["orcabusId"] == "wfl.abc123"
    lib_ids = {lib["libraryId"] for lib in payload["libraries"]}
    assert lib_ids == {TUMOR_ID, NORMAL_ID}


def test_submitted_event_includes_baseline(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    result = submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    events = mock_boto3["events"]
    entry = events.put_events.call_args[1]["Entries"][0]
    assert entry["DetailType"] == "SashRegressionRunSubmitted"
    detail = json.loads(entry["Detail"])
    assert detail["portalRunId"] == result["portal_run_id"]
    assert detail["newVersion"] == "0.7.0"
    assert detail["baselineVersion"] == "0.6.4"


# -- Existing run paths --

def test_existing_succeeded_skips_submission(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    existing_run = {
        "portalRunId": "20260101existing",
        "workflowRunName": "umccr_tested_sash_0_7_0_vs_0_6_4_existingid",
        "libraries": [
            {"libraryId": TUMOR_ID},
            {"libraryId": NORMAL_ID},
        ],
        "currentState": {"status": "SUCCEEDED"},
    }

    def get_with_existing(url, params=None, **kw):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "workflowrun" in url:
            resp.json.return_value = {"results": [existing_run]}
        else:
            resp.json.return_value = {"results": [MOCK_WORKFLOW]}
        return resp

    mock_requests.get.side_effect = get_with_existing

    result = submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    assert result["action"] == "already_succeeded"
    assert result["portal_run_id"] == "20260101existing"
    mock_boto3["lambda"].invoke.assert_not_called()


def test_existing_running_skips_submission(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    existing_run = {
        "portalRunId": "20260101running",
        "workflowRunName": "umccr_tested_sash_0_7_0_vs_0_6_4_runningid",
        "libraries": [{"libraryId": TUMOR_ID}, {"libraryId": NORMAL_ID}],
        "currentState": {"status": "RUNNING"},
    }

    def get_with_existing(url, params=None, **kw):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "workflowrun" in url:
            resp.json.return_value = {"results": [existing_run]}
        else:
            resp.json.return_value = {"results": [MOCK_WORKFLOW]}
        return resp

    mock_requests.get.side_effect = get_with_existing

    result = submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    assert result["action"] == "already_running"
    mock_boto3["lambda"].invoke.assert_not_called()


def test_different_libraries_not_matched(mock_boto3, mock_requests):
    """A run with the same version but different libraries should not count as existing."""
    from submitter.submit import submit_sash_run

    existing_run = {
        "portalRunId": "20260101other",
        "workflowRunName": "umccr_tested_sash_0_7_0_vs_0_6_4_otherid",
        "libraries": [{"libraryId": "L9999999"}, {"libraryId": "L8888888"}],
        "currentState": {"status": "SUCCEEDED"},
    }

    def get_with_existing(url, params=None, **kw):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "workflowrun" in url:
            resp.json.return_value = {"results": [existing_run]}
        elif "metadata" in url:
            lib_id = (params or {}).get("libraryId", "")
            lib = MOCK_TUMOR_LIB if lib_id == TUMOR_ID else MOCK_NORMAL_LIB
            resp.json.return_value = {"results": [lib]}
        else:
            resp.json.return_value = {"results": [MOCK_WORKFLOW]}
        return resp

    mock_requests.get.side_effect = get_with_existing

    result = submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")
    assert result["action"] == "submitted"


# -- Error paths --

def test_wru_validator_error_raises(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    mock_boto3["lambda"].invoke.return_value = {
        "Payload": MagicMock(read=lambda: json.dumps({"statusCode": 500, "body": "error"}).encode())
    }
    with pytest.raises(RuntimeError, match="WruDraftValidator error"):
        submit_sash_run(TUMOR_ID, NORMAL_ID, "0.7.0", "0.6.4")


def test_missing_workflow_raises(mock_boto3, mock_requests):
    from submitter.submit import submit_sash_run

    def no_workflow(url, params=None, **kw):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"results": []}
        return resp

    mock_requests.get.side_effect = no_workflow
    with pytest.raises(ValueError, match="No workflow found"):
        submit_sash_run(TUMOR_ID, NORMAL_ID, "9.9.9", "0.6.4")
