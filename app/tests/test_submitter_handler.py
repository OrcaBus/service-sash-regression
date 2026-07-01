"""Unit tests for the Submitter Lambda handler."""

import json
import pytest
from unittest.mock import patch

ENV = {
    "ORCABUS_TOKEN_SECRET_ID": "orcabus/token",
    "HOSTNAME_SSM_PARAMETER_NAME": "/hosted_zone/umccr/name",
    "WRU_VALIDATOR_LAMBDA_NAME": "WruDraftValidator",
    "EVENTS_BUS_NAME": "OrcaBusMain",
    "TESTDATA_TUMOR_LIBRARY_ID": "L2301218",
    "TESTDATA_NORMAL_LIBRARY_ID": "L2301217",
}

SUBMIT_RESULT = {"portal_run_id": "20260603abcd1234", "action": "submitted"}


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def mock_submit():
    with patch("submitter.lambdas.submitter.handler.submit_sash_run") as m:
        m.return_value = SUBMIT_RESULT
        yield m


def test_direct_invocation(mock_submit):
    from submitter.lambdas.submitter.handler import handler

    result = handler({"new_version": "0.7.0", "baseline_version": "0.6.4"}, None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["action"] == "submitted"
    assert body["portal_run_id"] == "20260603abcd1234"
    mock_submit.assert_called_once_with("L2301218", "L2301217", "0.7.0", "0.6.4")


def test_api_gateway_invocation(mock_submit):
    from submitter.lambdas.submitter.handler import handler

    event = {"body": json.dumps({"new_version": "0.8.0", "baseline_version": "0.7.0"})}
    result = handler(event, None)
    assert result["statusCode"] == 200
    mock_submit.assert_called_once_with("L2301218", "L2301217", "0.8.0", "0.7.0")


def test_library_id_override(mock_submit):
    from submitter.lambdas.submitter.handler import handler

    result = handler({
        "new_version": "0.7.0",
        "baseline_version": "0.6.4",
        "tumor_library_id": "L9999999",
        "normal_library_id": "L8888888",
    }, None)
    assert result["statusCode"] == 200
    mock_submit.assert_called_once_with("L9999999", "L8888888", "0.7.0", "0.6.4")


def test_missing_new_version_raises():
    from submitter.lambdas.submitter.handler import handler

    with pytest.raises(ValueError, match="new_version and baseline_version are required"):
        handler({"baseline_version": "0.6.4"}, None)


def test_missing_baseline_version_raises():
    from submitter.lambdas.submitter.handler import handler

    with pytest.raises(ValueError, match="new_version and baseline_version are required"):
        handler({"new_version": "0.7.0"}, None)
