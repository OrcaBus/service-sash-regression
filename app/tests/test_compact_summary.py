"""
Tests for comprehensive_sash_comparison._build_compact_summary — the PASS/FAIL threshold logic.

comprehensive_sash_comparison.py imports `run_logging` as a sibling module (it also runs as a
standalone script via subprocess in comparison.py), so it isn't part of the `comparator` package
import graph. Add the comparator/ dir to sys.path directly, matching how the script resolves
imports at runtime.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "comparator"))

from comprehensive_sash_comparison import NUMERIC_DELTA_EPSILON, _build_compact_summary  # noqa: E402


def _metrics(purity1=None, purity2=None, tmb1=None, tmb2=None, different_keys=None, missing=0):
    return {
        "file_comparison": {
            "different_keys": different_keys or [],
            "missing_run1": missing,
            "missing_run2": 0,
            "missing_both": 0,
        },
        "purple": {
            "run1": {"purity": purity1} if purity1 is not None else {},
            "run2": {"purity": purity2} if purity2 is not None else {},
        },
        "multiqc": {
            "run1": {"tmb": tmb1} if tmb1 is not None else {},
            "run2": {"tmb": tmb2} if tmb2 is not None else {},
        },
    }


class TestBuildCompactSummary:
    def test_identical_values_pass(self):
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(purity1=0.61, purity2=0.61))
        assert summary["status"] == "PASS"
        assert summary["critical_items"] == []

    def test_delta_below_epsilon_still_passes(self):
        summary = _build_compact_summary(
            "p", "T", "N", {}, _metrics(purity1=0.61, purity2=0.61 + NUMERIC_DELTA_EPSILON / 10)
        )
        assert summary["status"] == "PASS"

    def test_small_delta_now_fails_not_warns(self):
        """A 0.02 purity delta used to land in the WARN band (< 0.05); it's now a FAIL."""
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(purity1=0.61, purity2=0.63))
        assert summary["status"] == "FAIL"
        assert "purity_delta:0.020000" in summary["critical_items"]

    def test_tmb_delta_fails(self):
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(tmb1=7.38, tmb2=7.39))
        assert summary["status"] == "FAIL"
        assert any(item.startswith("tmb_delta:") for item in summary["critical_items"])

    def test_summary_has_no_warning_keys(self):
        """The WARN band is gone from the artifact schema, not just always-empty."""
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(purity1=0.61, purity2=0.615))
        assert summary["status"] == "FAIL"
        assert "warning_items" not in summary
        assert "warning_count" not in summary

    def test_changed_key_file_fails(self):
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(different_keys=["pcgr_tiers"]))
        assert summary["status"] == "FAIL"
        assert "changed_key_files:pcgr_tiers" in summary["critical_items"]

    def test_missing_files_fail(self):
        summary = _build_compact_summary("p", "T", "N", {}, _metrics(missing=1))
        assert summary["status"] == "FAIL"
        assert "missing_key_files:1" in summary["critical_items"]
