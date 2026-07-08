import os
from unittest.mock import patch

os.environ.setdefault("COMPARATOR_FUNCTION_NAME", "sash-regression-comparator")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")

from watcher.lambdas.watcher.handler import handler

_RUN_NAME = "umccr_tested_sash_0_7_1_vs_0_6_4_20260603abcd1234"


def _eb_event(
    run_name: str,
    status: str,
    output_uri: str = "s3://cache/sash/run/",
    sash_rel_path: str = "group123/",
) -> dict:
    return {
        "detail": {
            "portalRunId": "20260603abcd1234",  # pragma: allowlist secret
            "status": status,
            "workflowRunName": run_name,
            "workflow": {"name": "sash"},
            "payload": {
                "data": {
                    "engineParameters": {"outputUri": output_uri},
                    "outputs": {"sashRelPath": sash_rel_path},
                }
            },
        }
    }


def test_invokes_comparator_on_succeeded():
    with patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock:
        handler(_eb_event(_RUN_NAME, "SUCCEEDED", "s3://cache/new/", "group123/"), None)
    mock.assert_called_once_with(
        new_version="0.7.1",
        baseline_version="0.6.4",
        new_output_path="s3://cache/new/group123/",
    )


def test_ignores_untracked_run():
    with patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock:
        handler(_eb_event("other_pipeline_run", "SUCCEEDED"), None)
    mock.assert_not_called()


def test_does_not_invoke_comparator_on_failed():
    with patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock:
        handler(_eb_event(_RUN_NAME, "FAILED"), None)
    mock.assert_not_called()


def test_ignores_unknown_status():
    with patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock:
        handler(_eb_event(_RUN_NAME, "RUNNING"), None)
    mock.assert_not_called()
