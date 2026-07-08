import logging

from watcher.track import invoke_comparator, parse_run_name

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> None:
    detail = event["detail"]
    portal_run_id = detail["portalRunId"]
    status = detail["status"]
    workflow_run_name = detail.get("workflowRunName", "")

    parsed = parse_run_name(workflow_run_name)
    if parsed is None:
        logger.info(f"workflowRunName={workflow_run_name!r} not ours — ignoring")
        return

    new_version, baseline_version = parsed
    logger.info(
        f"Matched our run: portal_run_id={portal_run_id} status={status} "
        f"new={new_version} baseline={baseline_version}"
    )

    if status == "SUCCEEDED":
        # Sash's WRSC events carry a relative output path (outputs.sashRelPath), joined
        # against engineParameters.outputUri — same convention used by sibling pipeline
        # managers, e.g. dragen-wgts-dna's *OutputRelPath + engineParameters.outputUri.
        payload_data = detail["payload"]["data"]
        output_uri = payload_data["engineParameters"]["outputUri"]
        sash_rel_path = payload_data["outputs"]["sashRelPath"]
        new_output_path = output_uri + sash_rel_path
        invoke_comparator(
            new_version=new_version,
            baseline_version=baseline_version,
            new_output_path=new_output_path,
        )
    elif status == "FAILED":
        logger.warning(f"Sash run FAILED: portal_run_id={portal_run_id}")
