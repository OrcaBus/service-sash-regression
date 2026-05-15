import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Key sash output files — relative to the CANCER_REPORT dir, using {tumor} placeholder
EXPECTED_FILES = [
    "{tumor}.cancer_report.html",
    "{tumor}.chord.prediction.tsv",
    "{tumor}.hrdscore.csv",
    "{tumor}.pass.vcf.gz",
    "{tumor}.somatic.bcftools_stats.txt",
    "{tumor}.somatic.variant_counts_process.json",
    "{tumor}.cnv.prioritised.tsv",
    "{tumor}.sv.prioritised.tsv",
    "{tumor}.sv.prioritised.vcf.gz",
    "versions.yml",
]


def check_schema(run_dir: Path, tumor: str) -> dict:
    """
    Check that all expected files are present in run_dir/CANCER_REPORT/.
    Returns {"passed": bool, "missing": [...], "present": [...]}.
    """
    cancer_report = run_dir / "CANCER_REPORT"
    missing = []
    present = []

    for template in EXPECTED_FILES:
        fname = template.replace("{tumor}", tumor)
        if (cancer_report / fname).exists():
            present.append(fname)
        else:
            missing.append(fname)
            logger.warning(f"Missing expected file: {fname}")

    passed = len(missing) == 0
    if not passed:
        logger.error(f"Schema check FAILED — {len(missing)} missing files")
    else:
        logger.info(f"Schema check PASSED — all {len(present)} files present")

    return {"passed": passed, "missing": missing, "present": present}
