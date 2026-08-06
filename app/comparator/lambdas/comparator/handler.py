"""
Comparator Lambda — sash regression service.

Triggered manually with:
  {
    "new_version":      "0.7.0",
    "baseline_version": "0.6.4",
    "case_name":        "SEQC-II-medium"   # optional, defaults to first case in config
  }

Reads testdata config from S3 (TESTDATA_CONFIG_S3_URI env var),
downloads both sash output dirs, runs schema check + comparison,
uploads result summary to testdata S3 bucket.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml

from comparator.comparison import run_comparison
from comparator.s3_utils import download_s3_dir, parse_s3_uri, upload_file
from comparator.schema_check import check_schema

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TESTDATA_CONFIG_S3_URI = os.environ["TESTDATA_CONFIG_S3_URI"]
RESULT_S3_PREFIX = os.environ["RESULT_S3_PREFIX"]  # e.g. s3://test-data-.../testdata/analysis/production/sash/


def load_config(s3_uri: str) -> dict:
    bucket, key = parse_s3_uri(s3_uri)
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    return yaml.safe_load(obj["Body"].read())


def _derive_pair_compact_status(pair_result: dict) -> dict:
    """Build a compact pair status from comparison output (summary.json preferred)."""
    schema = pair_result.get("schema") or {}
    if not schema.get("passed"):
        return {
            "status": "FAIL",
            "critical_count": 1,
            "critical_items": ["schema_check_failed"],
            "metrics_impacted": True,
        }

    comparison = pair_result.get("comparison")
    if not comparison:
        return {
            "status": "FAIL",
            "critical_count": 1,
            "critical_items": ["comparison_missing"],
            "metrics_impacted": True,
        }

    # Preferred shape from summary.json
    if isinstance(comparison, dict) and "critical_items" in comparison and "status" in comparison:
        return {
            "status": comparison.get("status", "FAIL"),
            "critical_count": int(comparison.get("critical_count", 0)),
            "critical_items": comparison.get("critical_items", []),
            "metrics_impacted": bool(comparison.get("metrics_impacted", False)),
        }

    # Fallback for legacy metrics.json shape
    file_comparison = ((comparison.get("comparison") or {}).get("file_comparison") or {})
    different = int(file_comparison.get("different", 0))
    missing_total = (
        int(file_comparison.get("missing_run1", 0))
        + int(file_comparison.get("missing_run2", 0))
        + int(file_comparison.get("missing_both", 0))
    )
    if missing_total > 0:
        return {
            "status": "FAIL",
            "critical_count": 1,
            "critical_items": [f"missing_key_files:{missing_total}"],
            "metrics_impacted": True,
        }
    if different > 0:
        return {
            "status": "FAIL",
            "critical_count": 1,
            "critical_items": [f"changed_key_files:{different}"],
            "metrics_impacted": True,
        }
    return {
        "status": "PASS",
        "critical_count": 0,
        "critical_items": [],
        "metrics_impacted": False,
    }


def _apply_output_overrides(pairs: list, new_output_path: str, baseline_output_path: str) -> None:
    """Apply optional run path overrides to all selected pairs."""
    if new_output_path:
        for pair in pairs:
            pair["run2"] = new_output_path
    if baseline_output_path:
        for pair in pairs:
            pair["run1"] = baseline_output_path


def _run_pair(pair: dict, tmp_path: Path, new_version: str, baseline_version: str) -> dict:
    """Run schema check, comparison, and upload for one pair."""
    tumor = pair["tumor"]
    normal = pair["normal"]
    subject = pair["metadata"].get("subject", f"{tumor}_{normal}")

    logger.info(f"Processing {subject} ({tumor}/{normal})")

    run1_dir = tmp_path / "baseline" / f"{tumor}_{normal}"
    run2_dir = tmp_path / "new" / f"{tumor}_{normal}"
    download_s3_dir(pair["run1"], run1_dir)
    download_s3_dir(pair["run2"], run2_dir)

    schema_run1 = check_schema(run1_dir, tumor, normal)
    schema_run2 = check_schema(run2_dir, tumor, normal)
    schema_result = {
        "baseline": schema_run1,
        "new": schema_run2,
        "passed": schema_run1["passed"] and schema_run2["passed"],
    }

    if not schema_result["passed"]:
        logger.error(f"Schema check failed for {subject} — skipping comparison")
        return {"subject": subject, "schema": schema_result, "comparison": None}

    output_dir = tmp_path / "output" / f"{tumor}_{normal}"
    comparison_result = run_comparison(run1_dir, run2_dir, tumor, normal, output_dir)

    exec_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    case_id = pair["metadata"].get("case", f"{tumor}_{normal}")
    s3_out_prefix = f"{RESULT_S3_PREFIX.rstrip('/')}/{new_version}-vs-{baseline_version}/{case_id}/{exec_id}/test/"
    for output_file in output_dir.rglob("*"):
        if output_file.is_file():
            rel = output_file.relative_to(output_dir)
            upload_file(output_file, f"{s3_out_prefix}data/{rel}")

    return {
        "subject": subject,
        "schema": schema_result,
        "comparison": comparison_result,
        "s3_results": s3_out_prefix,
        "summary_s3_uri": f"{s3_out_prefix}data/summary.json",
    }


def _build_compact_summary(results: list, declared_update_type: str) -> dict:
    """Aggregate pair-level statuses into one compact summary.

    Pair status is one of PASS / FAIL / MANUAL_CHECK — there is no WARN band, so the run rolls up
    to FAIL if any pair failed, then MANUAL_CHECK, then PASS. See docs/comparison-thresholds.md.
    """
    pair_compact = [_derive_pair_compact_status(result) for result in results]
    pass_count = sum(1 for result in pair_compact if result["status"] == "PASS")
    fail_count = sum(1 for result in pair_compact if result["status"] == "FAIL")
    manual_check_count = sum(1 for result in pair_compact if result["status"] == "MANUAL_CHECK")

    if fail_count > 0:
        status = "FAIL"
    elif manual_check_count > 0:
        status = "MANUAL_CHECK"
    else:
        status = "PASS"

    critical_items = []
    for result in pair_compact:
        critical_items.extend(result.get("critical_items", []))

    return {
        "status": status,
        "declared_update_type": declared_update_type or None,
        "total_pairs": len(pair_compact),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "manual_check_count": manual_check_count,
        "critical_count": sum(result["critical_count"] for result in pair_compact),
        "critical_items": critical_items[:8],
        "metrics_impacted": any(result["metrics_impacted"] for result in pair_compact),
    }


def handler(event: dict, context) -> dict:
    new_version = event["new_version"]
    baseline_version = event["baseline_version"]
    case_name = event.get("case_name")
    new_output_path = event.get("new_output_path")
    baseline_output_path = event.get("baseline_output_path")
    declared_update_type = (event.get("update_type") or event.get("release_type") or "").strip().lower()

    logger.info(f"Comparing sash {new_version} vs {baseline_version}")

    config = load_config(TESTDATA_CONFIG_S3_URI)
    pairs = config["pairs"]

    if case_name:
        pairs = [p for p in pairs if p["metadata"].get("case") == case_name]
        if not pairs:
            raise ValueError(f"No case '{case_name}' in testdata config")

    _apply_output_overrides(pairs, new_output_path, baseline_output_path)

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for pair in pairs:
            results.append(_run_pair(pair, tmp_path, new_version, baseline_version))

    compact_summary = _build_compact_summary(results, declared_update_type)

    summary = {
        "new_version": new_version,
        "baseline_version": baseline_version,
        "results": results,
        "all_schema_passed": all(r["schema"]["passed"] for r in results),
        "compact_summary": compact_summary,
    }

    logger.info("FINAL_RESULT %s", json.dumps(compact_summary, default=str, separators=(",", ":")))
    return summary
