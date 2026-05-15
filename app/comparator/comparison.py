import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).parent / "comprehensive_sash_comparison.py"


def run_comparison(run1: Path, run2: Path, tumor: str, normal: str, output_dir: Path) -> dict:
    """
    Run comprehensive_sash_comparison.py on two sash output dirs.
    Returns parsed JSON summary.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(SCRIPT),
        "--run1", str(run1 / "CANCER_REPORT"),
        "--run2", str(run2 / "CANCER_REPORT"),
        "--tumor", tumor,
        "--normal", normal,
        "--output", str(output_dir),
        "--json-mode",
    ]
    logger.info(f"Running comparison: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"Comparison failed:\n{result.stderr}")
        raise RuntimeError(f"comprehensive_sash_comparison.py failed: {result.stderr[-500:]}")

    summary_path = output_dir / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text())
    return {"status": "completed", "output_dir": str(output_dir)}
