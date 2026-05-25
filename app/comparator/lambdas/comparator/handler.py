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


def handler(event: dict, context) -> dict:
    new_version = event["new_version"]
    baseline_version = event["baseline_version"]
    case_name = event.get("case_name")

    logger.info(f"Comparing sash {new_version} vs {baseline_version}")

    config = load_config(TESTDATA_CONFIG_S3_URI)
    pairs = config["pairs"]

    if case_name:
        pairs = [p for p in pairs if p["metadata"].get("case") == case_name]
        if not pairs:
            raise ValueError(f"No case '{case_name}' in testdata config")

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        for pair in pairs:
            tumor = pair["tumor"]
            normal = pair["normal"]
            subject = pair["metadata"].get("subject", f"{tumor}_{normal}")

            logger.info(f"Processing {subject} ({tumor}/{normal})")

            # Download both sash output dirs
            run1_dir = tmp_path / "baseline" / f"{tumor}_{normal}"
            run2_dir = tmp_path / "new" / f"{tumor}_{normal}"
            download_s3_dir(pair["run1"], run1_dir)
            download_s3_dir(pair["run2"], run2_dir)

            # Schema check
            schema_run1 = check_schema(run1_dir, tumor, normal)
            schema_run2 = check_schema(run2_dir, tumor, normal)

            schema_result = {
                "baseline": schema_run1,
                "new": schema_run2,
                "passed": schema_run1["passed"] and schema_run2["passed"],
            }

            if not schema_result["passed"]:
                logger.error(f"Schema check failed for {subject} — skipping comparison")
                results.append({"subject": subject, "schema": schema_result, "comparison": None})
                continue

            # Comparison
            output_dir = tmp_path / "output" / f"{tumor}_{normal}"
            comparison_result = run_comparison(run1_dir, run2_dir, tumor, normal, output_dir)

            # Upload output files to S3
            exec_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            case_id = pair["metadata"].get("case", f"{tumor}_{normal}")
            s3_out_prefix = f"{RESULT_S3_PREFIX.rstrip('/')}/{new_version}-vs-{baseline_version}/{case_id}/{exec_id}/test/"
            for f in output_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(output_dir)
                    upload_file(f, f"{s3_out_prefix}data/{rel}")

            results.append({
                "subject": subject,
                "schema": schema_result,
                "s3_results": s3_out_prefix,
            })

    summary = {
        "new_version": new_version,
        "baseline_version": baseline_version,
        "results": results,
        "all_schema_passed": all(r["schema"]["passed"] for r in results),
    }

    logger.info(f"Done: {json.dumps(summary, default=str)}")
    return summary
