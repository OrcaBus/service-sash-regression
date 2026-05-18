from pathlib import Path

import pytest

from comparator.schema_check import EXPECTED_FILES, check_schema


def _populate_cancer_report(run_dir: Path, tumor: str, filenames: list[str]) -> None:
    cancer_report = run_dir / "CANCER_REPORT"
    cancer_report.mkdir(parents=True)
    for f in filenames:
        (cancer_report / f).touch()


class TestCheckSchema:
    def test_all_expected_files_present_passes(self, tmp_path):
        tumor = "L2301218"
        files = [t.replace("{tumor}", tumor) for t in EXPECTED_FILES]
        _populate_cancer_report(tmp_path, tumor, files)

        result = check_schema(tmp_path, tumor)

        assert result["passed"] is True
        assert result["missing"] == []
        assert set(result["present"]) == set(files)

    def test_missing_one_file_fails(self, tmp_path):
        tumor = "L2301218"
        all_files = [t.replace("{tumor}", tumor) for t in EXPECTED_FILES]
        _populate_cancer_report(tmp_path, tumor, all_files[1:])  # omit first

        result = check_schema(tmp_path, tumor)

        assert result["passed"] is False
        assert all_files[0] in result["missing"]
        assert all_files[0] not in result["present"]

    def test_no_cancer_report_dir_fails_with_all_missing(self, tmp_path):
        result = check_schema(tmp_path, "L2301218")

        assert result["passed"] is False
        assert len(result["missing"]) == len(EXPECTED_FILES)
        assert result["present"] == []
