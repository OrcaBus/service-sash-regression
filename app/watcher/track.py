import json
import logging
import os
import re

import boto3

logger = logging.getLogger(__name__)

COMPARATOR_FUNCTION_NAME = os.environ["COMPARATOR_FUNCTION_NAME"]

# Matches: umccr_tested_sash_{new_slug}_vs_{base_slug}_{portal_run_id}
# portal_run_id is always YYYYMMDD + 8 hex chars = 16 chars
_RUN_NAME_RE = re.compile(
    r"^umccr_tested_sash_(?P<new>[0-9_]+)_vs_(?P<base>[0-9_]+)_[0-9a-f]{16}$"
)


def parse_run_name(workflow_run_name: str) -> tuple[str, str] | None:
    """Return (new_version, baseline_version) or None if not our run."""
    m = _RUN_NAME_RE.match(workflow_run_name)
    if not m:
        return None
    new_version = m.group("new").replace("_", ".")
    baseline_version = m.group("base").replace("_", ".")
    return new_version, baseline_version


def invoke_comparator(new_version: str, baseline_version: str, new_output_path: str) -> None:
    payload = {
        "new_version": new_version,
        "baseline_version": baseline_version,
        "new_output_path": new_output_path,
    }
    boto3.client("lambda").invoke(
        FunctionName=COMPARATOR_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
    logger.info(f"Invoked comparator async: new={new_version} baseline={baseline_version}")
