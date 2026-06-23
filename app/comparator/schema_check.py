import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Key sash output files — relative to run_dir, using {tumor}/{normal} placeholders
EXPECTED_FILES = [
    "{tumor}.cancer_report.html",
    "smlv_somatic/filter/{tumor}.pass.vcf.gz",
    "smlv_somatic/report/{tumor}.somatic.bcftools_stats.txt",
    "smlv_somatic/report/{tumor}.somatic.variant_counts_process.json",
    "sv_somatic/prioritise/{tumor}.cnv.prioritised.tsv",
    "sv_somatic/prioritise/{tumor}.sv.prioritised.tsv",
    "sv_somatic/prioritise/{tumor}.sv.prioritised.vcf.gz",
    "purple/{tumor}.purple.purity.tsv",
    "purple/{tumor}.purple.cnv.somatic.tsv",
]


def check_schema(run_dir: Path, tumor: str, normal: str = "") -> dict:
    """
    Check that all expected files are present in run_dir.
    Returns {"passed": bool, "missing": [...], "present": [...]}.
    """
    missing = []
    present = []

    for template in EXPECTED_FILES:
        rel = template.replace("{tumor}", tumor).replace("{normal}", normal)
        if (run_dir / rel).exists():
            present.append(rel)
        else:
            missing.append(rel)
            logger.warning(f"Missing expected file: {rel}")

    passed = len(missing) == 0
    if not passed:
        logger.error(f"Schema check FAILED — {len(missing)} missing files")
    else:
        logger.info(f"Schema check PASSED — all {len(present)} files present")

    return {"passed": passed, "missing": missing, "present": present}
