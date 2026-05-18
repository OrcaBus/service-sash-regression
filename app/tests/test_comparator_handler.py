"""
Tests for the comparator Lambda handler.

All AWS calls and inner comparator functions are mocked so no credentials or
network access are required.
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Must be set before the handler module is imported (module-level os.environ access)
os.environ.setdefault("TESTDATA_CONFIG_S3_URI", "s3://bucket/config.yaml")
os.environ.setdefault("RESULT_S3_PREFIX", "s3://bucket/results/")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")

from comparator.lambdas.comparator.handler import handler  # noqa: E402

_CONFIG = {
    "pairs": [
        {
            "tumor": "L2301218",
            "normal": "L2301217",
            "run1": "s3://bucket/sash/0.6.4/SEQC-II-medium/",
            "run2": "s3://bucket/sash/0.7.0/SEQC-II-medium/",
            "metadata": {"subject": "SBJ00480", "case": "SEQC-II-medium"},
        }
    ]
}

_SCHEMA_PASS = {"passed": True, "missing": [], "present": ["file.txt"]}
_SCHEMA_FAIL = {"passed": False, "missing": ["file.txt"], "present": []}
_CMP_RESULT = {"status": "ok"}


def _make_run_comparison(output_dir_holder: list):
    """Return a fake run_comparison that creates the output dir so iterdir() works."""

    def _fake(run1, run2, tumor, normal, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir_holder.append(output_dir)
        return _CMP_RESULT

    return _fake


class TestHandler:
    def test_returns_summary_for_successful_pair(self):
        captured = []
        with (
            patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
            patch("comparator.lambdas.comparator.handler.download_s3_dir"),
            patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_PASS),
            patch("comparator.lambdas.comparator.handler.run_comparison", side_effect=_make_run_comparison(captured)),
            patch("comparator.lambdas.comparator.handler.upload_file"),
        ):
            result = handler({"new_version": "0.7.0", "baseline_version": "0.6.4"}, None)

        assert result["new_version"] == "0.7.0"
        assert result["baseline_version"] == "0.6.4"
        assert result["all_schema_passed"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["comparison"] == _CMP_RESULT

    def test_skips_comparison_when_schema_fails(self):
        with (
            patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
            patch("comparator.lambdas.comparator.handler.download_s3_dir"),
            patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_FAIL),
            patch("comparator.lambdas.comparator.handler.run_comparison") as mock_cmp,
            patch("comparator.lambdas.comparator.handler.upload_file"),
        ):
            result = handler({"new_version": "0.7.0", "baseline_version": "0.6.4"}, None)

        mock_cmp.assert_not_called()
        assert result["all_schema_passed"] is False
        assert result["results"][0]["comparison"] is None

    def test_filters_pairs_by_case_name(self):
        two_case_config = {
            "pairs": [
                *_CONFIG["pairs"],
                {
                    "tumor": "T999",
                    "normal": "N999",
                    "run1": "s3://bucket/r1/",
                    "run2": "s3://bucket/r2/",
                    "metadata": {"subject": "SBJ99999", "case": "other-case"},
                },
            ]
        }
        captured = []
        with (
            patch("comparator.lambdas.comparator.handler.load_config", return_value=two_case_config),
            patch("comparator.lambdas.comparator.handler.download_s3_dir"),
            patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_PASS),
            patch("comparator.lambdas.comparator.handler.run_comparison", side_effect=_make_run_comparison(captured)),
            patch("comparator.lambdas.comparator.handler.upload_file"),
        ):
            result = handler(
                {"new_version": "0.7.0", "baseline_version": "0.6.4", "case_name": "SEQC-II-medium"},
                None,
            )

        assert len(result["results"]) == 1
        assert result["results"][0]["subject"] == "SBJ00480"

    def test_raises_on_unknown_case_name(self):
        with (
            patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
        ):
            with pytest.raises(ValueError, match="nonexistent"):
                handler(
                    {"new_version": "0.7.0", "baseline_version": "0.6.4", "case_name": "nonexistent"},
                    None,
                )
