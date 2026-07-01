"""
Submitter Lambda handler.

Accepts (API Gateway POST body or direct invocation):
  {
    "tumor_library_id":  "L2301218",   # optional, defaults to env TESTDATA_TUMOR_LIBRARY_ID
    "normal_library_id": "L2301217",   # optional, defaults to env TESTDATA_NORMAL_LIBRARY_ID
    "new_version":       "0.7.0",
    "baseline_version":  "0.6.4"
  }

Checks OrcaBus for an existing umccr_tested_ run. Submits via WruDraftValidator if needed.
Returns portal_run_id and action (submitted / already_running / already_succeeded).
"""

import json
import logging
import os

from submitter.submit import submit_sash_run

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TESTDATA_TUMOR_LIBRARY_ID = os.environ.get("TESTDATA_TUMOR_LIBRARY_ID", "L2301218")
TESTDATA_NORMAL_LIBRARY_ID = os.environ.get("TESTDATA_NORMAL_LIBRARY_ID", "L2301217")


def _parse_body(event: dict) -> dict:
    body = event.get("body")
    if body is not None:
        return json.loads(body) if isinstance(body, str) else body
    return event


def handler(event: dict, context) -> dict:
    logger.info("Submitter invoked")
    body = _parse_body(event)

    new_version = body.get("new_version")
    baseline_version = body.get("baseline_version")
    if not new_version or not baseline_version:
        raise ValueError("new_version and baseline_version are required")

    tumor_library_id = body.get("tumor_library_id", TESTDATA_TUMOR_LIBRARY_ID)
    normal_library_id = body.get("normal_library_id", TESTDATA_NORMAL_LIBRARY_ID)

    logger.info(f"new={new_version} baseline={baseline_version} tumor={tumor_library_id} normal={normal_library_id}")

    result = submit_sash_run(tumor_library_id, normal_library_id, new_version, baseline_version)
    logger.info(f"action={result['action']} portal_run_id={result['portal_run_id']}")

    return {
        "statusCode": 200,
        "body": json.dumps(result),
    }
