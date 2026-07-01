import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from comparator.comparison import run_comparison


def _make_mock_result(returncode: int, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stderr = stderr
    return m


class TestRunComparison:
    def test_prefers_summary_json_when_present(self, tmp_path):
        expected_summary = {"status": "PASS", "detected_severity": "minor", "critical_count": 0}

        def fake_subprocess(cmd, **kwargs):
            out_dir = Path(cmd[cmd.index("--output") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "summary.json").write_text(json.dumps(expected_summary))
            (out_dir / "metrics.json").write_text(json.dumps({"status": "legacy"}))
            return _make_mock_result(0)

        with patch("comparator.comparison.subprocess.run", side_effect=fake_subprocess):
            result = run_comparison(tmp_path / "r1", tmp_path / "r2", "L2301218", "L2301217", tmp_path / "out")

        assert result == expected_summary

    def test_returns_metrics_json_on_success(self, tmp_path):
        expected = {"status": "ok", "matches": 5}

        def fake_subprocess(cmd, **kwargs):
            assert "--json-mode" not in cmd, "--json-mode is not a valid script flag"
            out_dir = Path(cmd[cmd.index("--output") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "metrics.json").write_text(json.dumps(expected))
            return _make_mock_result(0)

        with patch("comparator.comparison.subprocess.run", side_effect=fake_subprocess):
            result = run_comparison(tmp_path / "r1", tmp_path / "r2", "L2301218", "L2301217", tmp_path / "out")

        assert result == expected

    def test_raises_on_non_zero_returncode(self, tmp_path):
        with patch("comparator.comparison.subprocess.run", return_value=_make_mock_result(1, "boom")):
            with pytest.raises(RuntimeError, match="comprehensive_sash_comparison"):
                run_comparison(tmp_path / "r1", tmp_path / "r2", "T", "N", tmp_path / "out")

    def test_returns_status_dict_when_no_metrics_json(self, tmp_path):
        def fake_subprocess(cmd, **kwargs):
            out_dir = Path(cmd[cmd.index("--output") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            return _make_mock_result(0)

        with patch("comparator.comparison.subprocess.run", side_effect=fake_subprocess):
            result = run_comparison(tmp_path / "r1", tmp_path / "r2", "T", "N", tmp_path / "out")

        assert result["status"] == "completed"
        assert "output_dir" in result
