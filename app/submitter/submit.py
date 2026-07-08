"""
Submit a sash regression run on testdata via OrcaBus.

Follows the same pattern as:
  service-sash-pipeline-manager/docs/operation/SOP/PM.SH.1/generate-WRU-draft.sh

Accepts tumor/normal library IDs, new_version, and baseline_version.
Checks OrcaBus for an existing umccr_tested_ run matching the same codeVersion + libraries:
  - SUCCEEDED with same codeVersion  -> skip to Comparator (action=already_succeeded)
  - Running with same codeVersion    -> skip, Watcher handles it (action=already_running)
  - No match or different codeVersion -> submit new run (action=submitted)

workflowRunName encodes both versions so the Watcher can extract the baseline
without a DB lookup: umccr_tested_sash_{new_ver}_vs_{baseline_ver}_{portal_run_id}
"""

import json
import logging
import os
import random
from datetime import datetime, timezone

import boto3
import requests

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "sash"
PAYLOAD_VERSION = "2025.08.05"
WORKFLOW_RUN_NAME_PREFIX = "umccr_tested"

ORCABUS_TOKEN_SECRET_ID = os.environ["ORCABUS_TOKEN_SECRET_ID"]
HOSTNAME_SSM_PARAMETER_NAME = os.environ["HOSTNAME_SSM_PARAMETER_NAME"]
WRU_VALIDATOR_LAMBDA_NAME = os.environ["WRU_VALIDATOR_LAMBDA_NAME"]
EVENTS_BUS_NAME = os.environ["EVENTS_BUS_NAME"]
ICA_PROJECT_ID = os.environ["ICA_PROJECT_ID"]
PIPELINE_CACHE_BUCKET = os.environ["PIPELINE_CACHE_BUCKET"]

_token_cache: str | None = None
_hostname_cache: str | None = None


def _orcabus_token() -> str:
    global _token_cache
    if _token_cache is None:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=ORCABUS_TOKEN_SECRET_ID)
        _token_cache = json.loads(secret["SecretString"])["id_token"]
    return _token_cache


def _hostname() -> str:
    global _hostname_cache
    if _hostname_cache is None:
        ssm = boto3.client("ssm")
        _hostname_cache = ssm.get_parameter(Name=HOSTNAME_SSM_PARAMETER_NAME)["Parameter"]["Value"]
    return _hostname_cache


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {_orcabus_token()}"}


def _create_portal_run_id() -> str:
    suffix = "".join(random.choices("0123456789abcdef", k=8))
    return datetime.now(timezone.utc).strftime("%Y%m%d") + suffix


def _workflow_run_name(new_version: str, baseline_version: str, portal_run_id: str) -> str:
    new_slug = new_version.replace(".", "_")
    base_slug = baseline_version.replace(".", "_")
    return f"{WORKFLOW_RUN_NAME_PREFIX}_sash_{new_slug}_vs_{base_slug}_{portal_run_id}"


def _get(path: str, subdomain: str, params: dict | None = None) -> dict:
    host = _hostname()
    resp = requests.get(
        f"https://{subdomain}.{host}/{path}",
        params=params,
        headers=_auth_header(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_workflow(version: str) -> dict:
    data = _get("api/v1/workflow", "workflow", params={"name": WORKFLOW_NAME, "version": version})
    results = data.get("results", [])
    if not results:
        raise ValueError(f"No workflow found: name=sash version={version}")
    return results[0]


def _get_library(library_id: str) -> dict:
    data = _get("api/v1/library", "metadata", params={"libraryId": library_id})
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Library {library_id} not found in metadata manager")
    lib = results[0]
    return {"libraryId": lib["libraryId"], "orcabusId": lib["orcabusId"]}


def _find_existing_run(code_version: str, tumor_library_id: str, normal_library_id: str) -> dict | None:
    """
    Return an existing umccr_tested_ run for the given codeVersion and libraries, or None.
    Queries by workflow name + codeVersion, then filters client-side.
    """
    data = _get(
        "api/v1/workflowrun",
        "workflow",
        params={"workflow__name": WORKFLOW_NAME, "workflow__codeVersion": code_version},
    )
    runs = data.get("results", [])
    expected_lib_ids = {tumor_library_id, normal_library_id}

    for run in runs:
        if not run.get("workflowRunName", "").startswith(WORKFLOW_RUN_NAME_PREFIX):
            continue
        run_lib_ids = {lib["libraryId"] for lib in run.get("libraries", [])}
        if run_lib_ids == expected_lib_ids:
            return run

    return None


def _get_payload_data(portal_run_id: str) -> dict:
    data = _get(
        "api/v1/payload",
        "workflow",
        params={"state__workflowRun__portalRunId": portal_run_id},
    )
    results = data.get("results", [])
    return results[0]["data"] if results else {}


def _find_prior_sash_inputs(tumor_library_id: str, normal_library_id: str) -> dict:
    """
    Find tags/inputs from the most recent SUCCEEDED sash run (any codeVersion) for this
    exact library pair, to seed a new DRAFT's payload.data.

    sash's inputs (dragenSomaticDir/dragenGermlineDir/oncoanalyserDnaDir — upstream DNA
    pipeline outputs) don't change between the sash versions under regression test, so we
    reuse them from whatever prior sash run already exists for these libraries rather than
    looking them up from dragen-wgts-dna/oncoanalyser-wgts-dna directly.
    """
    data = _get(
        "api/v1/workflowrun",
        "workflow",
        params={
            "workflow__name": WORKFLOW_NAME,
            "libraries__libraryId": tumor_library_id,
            "ordering": "-id",
        },
    )
    for run in data.get("results", []):
        if run.get("currentState", {}).get("status") != "SUCCEEDED":
            continue
        payload_data = _get_payload_data(run["portalRunId"])
        tags = payload_data.get("tags", {})
        if tags.get("tumorLibraryId") == tumor_library_id and tags.get("libraryId") == normal_library_id:
            return {"tags": tags, "inputs": payload_data.get("inputs", {})}

    raise ValueError(
        f"No prior SUCCEEDED sash run found for tumor={tumor_library_id} normal={normal_library_id}; "
        "cannot determine dragenSomaticDir/dragenGermlineDir/oncoanalyserDnaDir inputs. "
        "Run sash manually for this library pair at least once before regression-testing it."
    )


def _build_engine_parameters(portal_run_id: str, pipeline_id: str) -> dict:
    """
    Build sash's engineParameters for a new DRAFT run.

    Unlike tags/inputs (reused from a prior sash run — see _find_prior_sash_inputs),
    cacheUri/logsUri/outputUri are scoped to this run's own portal_run_id and must be
    built fresh: reusing a prior run's URIs would make ICA write this run's outputs into
    the prior run's S3 prefix instead of its own.
    """
    base = f"s3://{PIPELINE_CACHE_BUCKET}/byob-icav2/development"
    return {
        "cacheUri": f"{base}/cache/sash/{portal_run_id}/",
        "logsUri": f"{base}/logs/sash/{portal_run_id}/",
        "outputUri": f"{base}/analysis/sash/{portal_run_id}/",
        "projectId": ICA_PROJECT_ID,
        "pipelineId": pipeline_id,
        "analysisStorageSize": "SMALL",
    }


def _build_draft_payload(
    workflow: dict,
    libraries: list[dict],
    portal_run_id: str,
    workflow_run_name: str,
    data: dict,
) -> dict:
    return {
        "status": "DRAFT",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workflow": workflow,
        "workflowRunName": workflow_run_name,
        "portalRunId": portal_run_id,
        "libraries": libraries,
        "payload": {"version": PAYLOAD_VERSION, "data": data},
    }


def _invoke_wru_validator(payload: dict) -> None:
    lam = boto3.client("lambda")
    response = lam.invoke(
        FunctionName=WRU_VALIDATOR_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    result = json.loads(response["Payload"].read())
    if result.get("statusCode", 200) != 200:
        raise RuntimeError(f"WruDraftValidator error: {result}")


def _emit_submitted_event(
    portal_run_id: str,
    new_version: str,
    baseline_version: str,
    workflow_run_name: str,
    tumor_library_id: str,
    normal_library_id: str,
) -> None:
    events = boto3.client("events")
    events.put_events(
        Entries=[
            {
                "Source": "sash-regression.submitter",
                "DetailType": "SashRegressionRunSubmitted",
                "Detail": json.dumps({
                    "portalRunId": portal_run_id,
                    "newVersion": new_version,
                    "baselineVersion": baseline_version,
                    "workflowRunName": workflow_run_name,
                    "tumorLibraryId": tumor_library_id,
                    "normalLibraryId": normal_library_id,
                }),
                "EventBusName": EVENTS_BUS_NAME,
            }
        ]
    )


def submit_sash_run(
    tumor_library_id: str,
    normal_library_id: str,
    new_version: str,
    baseline_version: str,
) -> dict:
    """
    Submit a sash testdata regression run if needed.

    Returns a dict with:
      portal_run_id: str
      action: "submitted" | "already_running" | "already_succeeded"
    """
    workflow = _get_workflow(new_version)
    code_version = workflow.get("codeVersion", "")

    existing = _find_existing_run(code_version, tumor_library_id, normal_library_id)
    if existing:
        status = existing.get("currentState", {}).get("status", "")
        logger.info(f"Existing umccr_tested_ run found: {existing['portalRunId']} status={status}")
        if status == "SUCCEEDED":
            return {"portal_run_id": existing["portalRunId"], "action": "already_succeeded"}
        return {"portal_run_id": existing["portalRunId"], "action": "already_running"}

    libraries = [
        _get_library(tumor_library_id),
        _get_library(normal_library_id),
    ]
    draft_data = _find_prior_sash_inputs(tumor_library_id, normal_library_id)
    portal_run_id = _create_portal_run_id()
    run_name = _workflow_run_name(new_version, baseline_version, portal_run_id)
    draft_data["engineParameters"] = _build_engine_parameters(
        portal_run_id, workflow.get("executionEnginePipelineId", "")
    )
    payload = _build_draft_payload(workflow, libraries, portal_run_id, run_name, draft_data)

    logger.info(f"Submitting: portal_run_id={portal_run_id} run_name={run_name}")
    _invoke_wru_validator(payload)
    _emit_submitted_event(portal_run_id, new_version, baseline_version, run_name, tumor_library_id, normal_library_id)

    return {"portal_run_id": portal_run_id, "action": "submitted"}
