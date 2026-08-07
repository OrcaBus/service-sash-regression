#!/usr/bin/env python3
"""
Comprehensive SASH Run Comparison Script
========================================

This script performs an exhaustive comparison between two SASH runs, analyzing:
- All VCF files and variant counts
- BCFtools statistics for somatic and germline variants
- PCGR/CPSR tier distributions and overlaps
- Purple metrics (purity, ploidy, drivers, etc.)
- Structural variant analysis
- MultiQC metrics comparison
- Pipeline step-by-step counts
- Detailed variant overlap analysis
- Quality metrics and filtering statistics

Usage:
    # Single pair mode
    python comprehensive_sash_comparison.py \
        --run1 /path/to/run1/L2100780_L2100779 \
        --run2 /path/to/run2/L2100780_L2100779 \
        --tumor L2100780 \
        --normal L2100779 \
        --output comparison_output

    # Batch mode with config file
    python comprehensive_sash_comparison.py \
        --config runs-config.yaml \
        --output batch_comparison
"""

import argparse
import gzip
import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from run_logging import setup_run_logging

# Try to import VCF libraries
try:
    import pysam  # noqa: F401
    HAS_PYSAM = True
except ImportError:
    HAS_PYSAM = False

try:
    from cyvcf2 import VCF  # noqa: F401
    HAS_CYVCF2 = True
except ImportError:
    HAS_CYVCF2 = False

# Any purity/ploidy/TMB/MSI delta at or above this is treated as a real difference (FAIL), not
# floating-point noise. See docs/comparison-thresholds.md.
NUMERIC_DELTA_EPSILON = 1e-6


# ---------------------------------------------------------------------------
# Utility functions for cleaner pandas-based parsing
# ---------------------------------------------------------------------------

def read_tsv_file(path: str) -> pd.DataFrame:
    """Read TSV file (plain or gzipped) into DataFrame."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep='\t', low_memory=False)
    except Exception:
        return pd.DataFrame()


def normalise_tier_value(key: str) -> str:
    """Normalise tier labels (e.g., 'TIER 2' -> '2', 'II' -> '2')."""
    if not key:
        return ''
    text = str(key).strip().upper()
    roman = {'I': '1', 'II': '2', 'III': '3', 'IV': '4', 'V': '5'}
    if text in roman:
        return roman[text]
    m = re.search(r'TIER[_\s-]*([A-Z0-9]+)', text)
    if m:
        tok = m.group(1)
        if tok in roman:
            return roman[tok]
        if tok.isdigit():
            return tok.lstrip('0') or '0'
    digit = re.search(r'(\d+)', text)
    return digit.group(1).lstrip('0') or '0' if digit else key.strip()


def clean_for_json(obj: Any) -> Any:
    """Convert NaN/Inf values to None and sets to lists for valid JSON serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, set):
        # Convert set to sorted list and recursively clean its contents
        return sorted([clean_for_json(item) for item in obj], key=str)
    elif isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [clean_for_json(item) for item in obj]
    else:
        return obj


class SashRunAnalyzer:
    """Analyzer for SASH run results."""

    def __init__(self, run_dir: str, tumor: str, normal: str):
        self.run_dir = run_dir
        self.tumor = tumor
        self.normal = normal
        self.base_dir = self._get_base_dir()

    def _get_base_dir(self) -> str:
        """Get the base results directory."""
        p = Path(self.run_dir)

        # Try multiple patterns based on actual SASH structures:
        # 1. run_dir/{tumor}__{normal}/ (if run_dir is already at double underscore level)
        # 2. run_dir/{tumor}_{normal}/{tumor}__{normal}/ (new 0.7.0: single underscore dir, then double)
        # 3. run_dir/{tumor}_{normal}/{tumor}_{normal}/ (old 0.6.X: single underscore dir, then same)
        # 4. run_dir/{tumor}__{normal}/ (direct double underscore under run_dir)

        # If run_dir itself is already the pair directory (double or single underscore)
        if p.name == f"{self.tumor}__{self.normal}":
            return str(p)

        if p.name == f"{self.tumor}_{self.normal}":
            return str(p)

        # If there's a single-underscore directory under p (common layout), prefer it
        single_dir = p / f"{self.tumor}_{self.normal}"
        if single_dir.exists():
            # New 0.7.0: single_dir/{tumor}__{normal}/
            candidate = single_dir / f"{self.tumor}__{self.normal}"
            if candidate.exists():
                return str(candidate)

            # Old 0.6.x: the single_dir itself is the run directory
            return str(single_dir)

        # Try direct double underscore under run_dir
        candidate = p / f"{self.tumor}__{self.normal}"
        if candidate.exists():
            return str(candidate)

        # Fallback: return the double underscore path even if it doesn't exist yet
        return str(p / f"{self.tumor}__{self.normal}")

    def _read_text_file(self, path: str) -> list[str]:
        """Read text file, handling both regular and gzipped files."""
        if not os.path.exists(path):
            return []

        try:
            if path.endswith('.gz'):
                with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as f:
                    return f.read().splitlines()
            else:
                with open(path, 'rt', encoding='utf-8', errors='ignore') as f:
                    return f.read().splitlines()
        except Exception as e:
            print(f"Error reading {path}: {e}")
            return []

    def _read_json_file(self, path: str) -> Any:
        """Read JSON file, supporting plain text and gzipped inputs."""
        if not os.path.exists(path):
            return {}

        try:
            if path.endswith('.gz'):
                with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as f:
                    return json.load(f)
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading JSON {path}: {e}")
            return {}

    def _stringify_value(self, value: Any) -> Any:
        """Normalise complex JSON values for stable comparison output."""
        if value is None:
            return 'null'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, sort_keys=True)
        except TypeError:
            return str(value)

    def _normalise_tier_key(self, key: str) -> str:
        """Normalise tier labels so 'TIER 2' and '2' align."""
        if key is None:
            return ''
        text = str(key).strip()
        if not text:
            return ''

        upper = text.upper()
        roman_map = {
            'I': '1', 'II': '2', 'III': '3', 'IV': '4', 'V': '5',
            'VI': '6', 'VII': '7', 'VIII': '8', 'IX': '9', 'X': '10'
        }

        # Direct roman numerals
        if upper in roman_map:
            return roman_map[upper]

        # Extract digits from tier-like prefixes
        tier_patterns = [
            r'^(?:PCGR[_\s-]*)?TIER[_\s-]*([A-Z0-9]+)$',
            r'^(?:ACTIONABILITY[_\s-]*)?TIER[_\s-]*([A-Z0-9]+)$',
            r'^(?:FINAL[_\s-]*)?TIER[_\s-]*([A-Z0-9]+)$'
        ]
        for pattern in tier_patterns:
            match = re.match(pattern, upper)
            if match:
                token = match.group(1)
                if token in roman_map:
                    return roman_map[token]
                if token.isdigit():
                    return token.lstrip('0') or '0'
                digit_match = re.search(r'(\d+)', token)
                if digit_match:
                    return digit_match.group(1).lstrip('0') or '0'
                return token

        # Fallback to any digits present
        digit_match = re.search(r'(\d+)', upper)
        if digit_match:
            return digit_match.group(1).lstrip('0') or '0'

        return text

    def _calculate_file_md5(self, path: str) -> str | None:
        """Calculate MD5 checksum of a file."""
        if not os.path.exists(path):
            return None

        try:
            hash_md5 = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            print(f"Error calculating MD5 for {path}: {e}")
            return None

    def parse_bcftools_stats(self, path: str) -> dict[str, Any]:
        """Parse bcftools stats file."""
        lines = self._read_text_file(path)
        if not lines:
            return {}

        stats = {
            'records': None, 'snps': None, 'mnps': None, 'indels': None,
            'others': None, 'ts': None, 'tv': None, 'ts_tv': None,
            'multiallelic_sites': None, 'multiallelic_snp_sites': None
        }

        for line in lines:
            if line.startswith('SN\t0\tnumber of records:'):
                stats['records'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of SNPs:'):
                stats['snps'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of MNPs:'):
                stats['mnps'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of indels:'):
                stats['indels'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of others:'):
                stats['others'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of multiallelic sites:'):
                stats['multiallelic_sites'] = int(line.split('\t')[-1])
            elif line.startswith('SN\t0\tnumber of multiallelic SNP sites:'):
                stats['multiallelic_snp_sites'] = int(line.split('\t')[-1])
            elif line.startswith('TSTV\t0\t'):
                parts = line.split('\t')
                if len(parts) >= 5:
                    stats['ts'] = int(parts[2])
                    stats['tv'] = int(parts[3])
                    stats['ts_tv'] = float(parts[4])

        return stats

    def count_vcf_variants(self, vcf_path: str) -> dict[str, Any]:
        """Count variants in VCF file with detailed breakdown using proper VCF libraries."""
        if not os.path.exists(vcf_path):
            return {'total': 0, 'by_type': {}, 'by_filter': {}, 'by_chromosome': {}}

        # Try using cyvcf2 first (fastest)
        if HAS_CYVCF2:
            return self._count_vcf_cyvcf2(vcf_path)
        # Fallback to pysam
        elif HAS_PYSAM:
            return self._count_vcf_pysam(vcf_path)
        # Fallback to manual parsing
        else:
            return self._count_vcf_manual(vcf_path)

    def _count_vcf_cyvcf2(self, vcf_path: str) -> dict[str, Any]:
        """Count variants using cyvcf2 library."""
        try:
            from cyvcf2 import VCF
            vcf = VCF(vcf_path)

            total = 0
            by_type = Counter()
            by_filter = Counter()
            by_chromosome = Counter()
            mnv_tagged = 0

            for variant in vcf:
                total += 1

                # Count by chromosome
                by_chromosome[variant.CHROM] += 1

                # Count by filter
                filter_val = 'PASS' if variant.FILTER is None else ';'.join(variant.FILTER)
                by_filter[filter_val] += 1

                # Determine variant type
                ref = variant.REF
                alt = variant.ALT[0] if variant.ALT else ""

                if len(ref) == 1 and len(alt) == 1:
                    by_type['SNV'] += 1
                elif len(ref) > len(alt):
                    by_type['Deletion'] += 1
                elif len(ref) < len(alt):
                    by_type['Insertion'] += 1
                else:
                    by_type['Complex'] += 1

                # Check for MNV tags
                mnvtag = variant.INFO.get('MNVTAG')
                if mnvtag is not None and mnvtag != '.':
                    mnv_tagged += 1

            vcf.close()

            return {
                'total': total,
                'by_type': dict(by_type),
                'by_filter': dict(by_filter),
                'by_chromosome': dict(by_chromosome),
                'mnv_tagged': mnv_tagged
            }
        except Exception as e:
            print(f"Error using cyvcf2 for {vcf_path}: {e}")
            return self._count_vcf_manual(vcf_path)

    def _count_vcf_pysam(self, vcf_path: str) -> dict[str, Any]:
        """Count variants using pysam library."""
        try:
            import pysam
            vcf = pysam.VariantFile(vcf_path)

            total = 0
            by_type = Counter()
            by_filter = Counter()
            by_chromosome = Counter()
            mnv_tagged = 0

            for variant in vcf:
                total += 1

                # Count by chromosome
                by_chromosome[variant.chrom] += 1

                # Count by filter
                filter_val = 'PASS' if len(variant.filter) == 0 else ';'.join(variant.filter)
                by_filter[filter_val] += 1

                # Determine variant type
                ref = variant.ref
                alt = variant.alts[0] if variant.alts else ""

                if len(ref) == 1 and len(alt) == 1:
                    by_type['SNV'] += 1
                elif len(ref) > len(alt):
                    by_type['Deletion'] += 1
                elif len(ref) < len(alt):
                    by_type['Insertion'] += 1
                else:
                    by_type['Complex'] += 1

                # Check for MNV tags
                mnvtag = variant.info.get('MNVTAG')
                if mnvtag is not None and mnvtag != '.':
                    mnv_tagged += 1

            vcf.close()

            return {
                'total': total,
                'by_type': dict(by_type),
                'by_filter': dict(by_filter),
                'by_chromosome': dict(by_chromosome),
                'mnv_tagged': mnv_tagged
            }
        except Exception as e:
            print(f"Error using pysam for {vcf_path}: {e}")
            return self._count_vcf_manual(vcf_path)

    def parse_cpsr_tier_counts(self, tsv_path: str) -> dict[str, int]:
        """Parse CPSR tiers using pandas with concise classification columns."""
        df = read_tsv_file(tsv_path)
        if df.empty:
            return {}

        candidates = ['CPSR_CLASSIFICATION', 'FINAL_CLASSIFICATION', 'CLASSIFICATION', 'TIER']
        col = next((c for c in candidates if c in df.columns), None)
        if col is None:
            return {}

        counts = df[col].dropna().value_counts().to_dict()
        counts['_classification_column_used'] = col
        return counts

    def _count_vcf_manual(self, vcf_path: str) -> dict[str, Any]:
        """Count variants using manual parsing (fallback method)."""
        lines = self._read_text_file(vcf_path)
        if not lines:
            return {'total': 0, 'by_type': {}, 'by_filter': {}, 'by_chromosome': {}}

        total = 0
        by_type = Counter()
        by_filter = Counter()
        by_chromosome = Counter()
        mnv_tagged = 0

        for line in lines:
            if line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) < 8:
                continue

            total += 1
            chrom = parts[0]
            ref = parts[3]
            alt = parts[4]
            filter_val = parts[6]
            info = parts[7]

            # Count by chromosome
            by_chromosome[chrom] += 1

            # Count by filter
            by_filter[filter_val] += 1

            # Determine variant type
            if len(ref) == 1 and len(alt) == 1:
                by_type['SNV'] += 1
            elif len(ref) > len(alt):
                by_type['Deletion'] += 1
            elif len(ref) < len(alt):
                by_type['Insertion'] += 1
            else:
                by_type['Complex'] += 1

            # Check for MNV tags
            if 'MNVTAG=' in info and 'MNVTAG=.' not in info:
                mnv_tagged += 1

        return {
            'total': total,
            'by_type': dict(by_type),
            'by_filter': dict(by_filter),
            'by_chromosome': dict(by_chromosome),
            'mnv_tagged': mnv_tagged
        }


    def parse_sv_vcf(self, vcf_path: str) -> dict[str, Any]:
        """Parse structural variant VCF using proper VCF libraries."""
        if not os.path.exists(vcf_path):
            return {'total': 0, 'by_type': {}, 'by_filter': {}}

        # Try using cyvcf2 first
        if HAS_CYVCF2:
            return self._parse_sv_cyvcf2(vcf_path)
        # Fallback to pysam
        elif HAS_PYSAM:
            return self._parse_sv_pysam(vcf_path)
        # Fallback to manual parsing
        else:
            return self._parse_sv_manual(vcf_path)

    def _parse_sv_cyvcf2(self, vcf_path: str) -> dict[str, Any]:
        """Parse SV VCF using cyvcf2."""
        try:
            from cyvcf2 import VCF
            vcf = VCF(vcf_path)

            total = 0
            by_type = Counter()
            by_filter = Counter()

            for variant in vcf:
                total += 1

                # Count by filter
                filter_val = 'PASS' if variant.FILTER is None else ';'.join(variant.FILTER)
                by_filter[filter_val] += 1

                # Extract SV type
                svtype = variant.INFO.get('SVTYPE')
                if svtype is None:
                    # Try to extract from ALT
                    alt = variant.ALT[0] if variant.ALT else ""
                    m = re.match(r'<([A-Z]+)>', alt)
                    svtype = m.group(1) if m else 'UNKNOWN'

                by_type[svtype] += 1

            vcf.close()

            return {
                'total': total,
                'by_type': dict(by_type),
                'by_filter': dict(by_filter)
            }
        except Exception as e:
            print(f"Error using cyvcf2 for SV {vcf_path}: {e}")
            return self._parse_sv_manual(vcf_path)

    def _parse_sv_pysam(self, vcf_path: str) -> dict[str, Any]:
        """Parse SV VCF using pysam."""
        try:
            import pysam
            vcf = pysam.VariantFile(vcf_path)

            total = 0
            by_type = Counter()
            by_filter = Counter()

            for variant in vcf:
                total += 1

                # Count by filter
                filter_val = 'PASS' if len(variant.filter) == 0 else ';'.join(variant.filter)
                by_filter[filter_val] += 1

                # Extract SV type
                svtype = variant.info.get('SVTYPE')
                if svtype is None:
                    # Try to extract from ALT
                    alt = variant.alts[0] if variant.alts else ""
                    m = re.match(r'<([A-Z]+)>', alt)
                    svtype = m.group(1) if m else 'UNKNOWN'

                by_type[svtype] += 1

            vcf.close()

            return {
                'total': total,
                'by_type': dict(by_type),
                'by_filter': dict(by_filter)
            }
        except Exception as e:
            print(f"Error using pysam for SV {vcf_path}: {e}")
            return self._parse_sv_manual(vcf_path)

    def _parse_sv_manual(self, vcf_path: str) -> dict[str, Any]:
        """Parse structural variant VCF using manual parsing."""
        lines = self._read_text_file(vcf_path)
        if not lines:
            return {'total': 0, 'by_type': {}, 'by_filter': {}}

        total = 0
        by_type = Counter()
        by_filter = Counter()

        for line in lines:
            if line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) < 8:
                continue

            total += 1
            alt = parts[4]
            filter_val = parts[6]
            info = parts[7]

            by_filter[filter_val] += 1

            # Extract SV type
            svtype = None
            for item in info.split(';'):
                if item.startswith('SVTYPE='):
                    svtype = item.split('=', 1)[1]
                    break

            if not svtype:
                m = re.match(r'<([A-Z]+)>', alt)
                if m:
                    svtype = m.group(1)

            by_type[svtype or 'UNKNOWN'] += 1

        return {
            'total': total,
            'by_type': dict(by_type),
            'by_filter': dict(by_filter)
        }

    def parse_tier_file(self, tsv_path: str, tier_col: str = 'TIER') -> dict[str, int]:
        """Parse tier TSV file using pandas."""
        df = read_tsv_file(tsv_path)
        if df.empty:
            return {}

        # Find tier column
        candidates = [tier_col, 'PCGR_TIER', 'ACTIONABILITY_TIER', 'ACTIONABILITY', 'CGC_TIER', 'TIER', 'Tier', 'tier']
        col = next((c for c in candidates if c in df.columns), None)
        if col is None:
            return {}

        # Count tiers using pandas
        tier_counts = df[col].dropna().apply(normalise_tier_value).value_counts().to_dict()
        tier_counts['_tier_column_used'] = col

        # Extract actionability labels if present
        if 'ACTIONABILITY' in df.columns:
            act_df = df[[col, 'ACTIONABILITY']].dropna()
            if not act_df.empty:
                act_df[col] = act_df[col].apply(normalise_tier_value)
                labels = act_df.groupby(col)['ACTIONABILITY'].agg(lambda x: x.mode().iloc[0] if len(x) > 0 else None)
                tier_counts['_actionability_labels'] = labels.to_dict()

        return tier_counts

    def enhanced_vcf_analysis(self) -> dict[str, Any]:
        """Enhanced VCF analysis with parsing and detailed comparisons."""
        vcf_analysis = {}

        # base_dir is already the pair directory from _get_base_dir()
        # Find all VCF files in the base directory
        vcf_files = []
        if os.path.exists(self.base_dir):
            for root, dirs, files in os.walk(self.base_dir):
                for file in files:
                    if file.endswith(('.vcf', '.vcf.gz')):
                        vcf_files.append(os.path.join(root, file))

        vcf_analysis['total_vcf_files'] = len(vcf_files)
        vcf_analysis['vcf_file_list'] = [os.path.relpath(f, self.base_dir) for f in vcf_files]

        # Build key VCF paths - try multiple patterns for each
        def find_vcf(patterns):
            for pattern in patterns:
                full_path = os.path.join(self.base_dir, pattern)
                if os.path.exists(full_path):
                    return pattern
            return patterns[0]  # Return first as default

        key_vcfs = {
            'purple_somatic': find_vcf([
                f'purple/{self.tumor}.purple.somatic.vcf.gz',
                f'{self.tumor}__{self.normal}/purple/{self.tumor}.purple.somatic.vcf.gz'
            ]),
            'purple_sv': find_vcf([
                f'purple/{self.tumor}.purple.sv.vcf.gz',
                f'{self.tumor}__{self.normal}/purple/{self.tumor}.purple.sv.vcf.gz'
            ]),
            'pcgr_pass': find_vcf([
                f'smlv_somatic/report/pcgr/{self.tumor}.pcgr_acmg.grch38.pass.vcf.gz',
                f'smlv_somatic/report/pcgr/{self.tumor}.pcgr.grch38.pass.vcf.gz',
                f'{self.tumor}__{self.normal}/smlv_somatic/report/pcgr/{self.tumor}.pcgr_acmg.grch38.pass.vcf.gz'
            ]),
            'cpsr_pass': find_vcf([
                f'smlv_germline/report/cpsr/{self.normal}.cpsr.grch38.pass.vcf.gz',
                f'smlv_germline/report/cpsr/{self.normal}.cpsr.pass.vcf.gz',
                f'{self.tumor}__{self.normal}/smlv_germline/report/cpsr/{self.normal}.cpsr.grch38.pass.vcf.gz'
            ]),
            'sage_somatic': find_vcf([
                f'smlv_somatic/{self.tumor}.sage.somatic.vcf.gz',
                f'{self.tumor}__{self.normal}/smlv_somatic/{self.tumor}.sage.somatic.vcf.gz'
            ]),
            'gridss_sv': find_vcf([
                f'sv/{self.tumor}.gridss.vcf.gz',
                f'{self.tumor}__{self.normal}/sv/{self.tumor}.gridss.vcf.gz'
            ])
        }

        vcf_analysis['key_vcf_analysis'] = {}
        for vcf_name, vcf_rel_path in key_vcfs.items():
            vcf_full_path = os.path.join(self.base_dir, vcf_rel_path)
            if os.path.exists(vcf_full_path):
                print(f"    analyzing {vcf_name}...")
                analysis = self.vcf_analysis(vcf_full_path, vcf_name)
                analysis['file_path'] = vcf_rel_path
                analysis['file_size_mb'] = round(os.path.getsize(vcf_full_path) / (1024 * 1024), 2)
                vcf_analysis['key_vcf_analysis'][vcf_name] = analysis
            else:
                vcf_analysis['key_vcf_analysis'][vcf_name] = {'status': 'missing', 'file_path': vcf_rel_path}

        return vcf_analysis

    def vcf_analysis(self, vcf_path: str, vcf_type: str) -> dict[str, Any]:
        """analysis of VCF file with detailed variant parsing."""
        analysis = {
            'total': 0,
            'by_type': {},
            'by_filter': {},
            'by_chromosome': {},
            'by_quality': {'high': 0, 'medium': 0, 'low': 0},
            'sample_details': {},
            'annotation_details': {},
            'header_info': {},
            'variant_examples': []
        }

        try:
            # Parse VCF header for metadata
            header_lines = []
            variant_count = 0

            opener = gzip.open if vcf_path.endswith('.gz') else open
            mode = 'rt' if vcf_path.endswith('.gz') else 'r'

            with opener(vcf_path, mode, encoding='utf-8', errors='ignore') as f:
                samples = []

                for line in f:
                    if line.startswith('##'):
                        header_lines.append(line.strip())
                        continue
                    elif line.startswith('#CHROM'):
                        # Parse header to get samples
                        header_parts = line.strip().split('\t')
                        if len(header_parts) > 9:
                            samples = header_parts[9:]
                        continue

                    # Parse variant line
                    variant_count += 1
                    if variant_count > 10000:  # Limit for performance
                        break

                    parts = line.strip().split('\t')
                    if len(parts) < 8:
                        continue

                    chrom, _pos, _var_id, ref, alt, qual, filter_val, info = parts[:8]

                    # Count by chromosome
                    analysis['by_chromosome'][chrom] = analysis['by_chromosome'].get(chrom, 0) + 1

                    # Count by filter
                    filters = filter_val.split(';') if filter_val != 'PASS' and filter_val != '.' else ['PASS']
                    for f in filters:
                        analysis['by_filter'][f] = analysis['by_filter'].get(f, 0) + 1

                    # Determine variant type
                    var_type = self._classify_variant_type(ref, alt)
                    analysis['by_type'][var_type] = analysis['by_type'].get(var_type, 0) + 1

                    # Quality scoring
                    try:
                        qual_score = float(qual) if qual != '.' else 0
                        if qual_score >= 100:
                            analysis['by_quality']['high'] += 1
                        elif qual_score >= 20:
                            analysis['by_quality']['medium'] += 1
                        else:
                            analysis['by_quality']['low'] += 1
                    except ValueError:
                        analysis['by_quality']['low'] += 1

                    # Parse INFO field for annotations
                    info_fields = {}
                    for item in info.split(';'):
                        if '=' in item:
                            key, value = item.split('=', 1)
                            info_fields[key] = value
                        else:
                            info_fields[item] = True

                    # Extract specific annotations based on VCF type
                    if vcf_type in ['pcgr_pass', 'cpsr_pass']:
                        self._extract_annotation_details(info_fields, analysis['annotation_details'])

                    # Collect sample information if available
                    if len(parts) > 9 and samples:
                        format_fields = parts[8].split(':') if len(parts) > 8 else []
                        for i, sample_name in enumerate(samples):
                            if len(parts) > 9 + i:
                                sample_data = parts[9 + i].split(':')
                                if sample_name not in analysis['sample_details']:
                                    analysis['sample_details'][sample_name] = {'genotypes': {}, 'depths': []}

                                # Extract genotype
                                if 'GT' in format_fields and len(sample_data) > format_fields.index('GT'):
                                    gt = sample_data[format_fields.index('GT')]
                                    analysis['sample_details'][sample_name]['genotypes'][gt] = \
                                        analysis['sample_details'][sample_name]['genotypes'].get(gt, 0) + 1

                                # Extract depth
                                if 'DP' in format_fields and len(sample_data) > format_fields.index('DP'):
                                    try:
                                        depth = int(sample_data[format_fields.index('DP')])
                                        analysis['sample_details'][sample_name]['depths'].append(depth)
                                    except (ValueError, IndexError):
                                        pass

                    # Store examples of interesting variants
                    if len(analysis['variant_examples']) < 10 and (filter_val == 'PASS' or var_type in ['SNV', 'INDEL']):
                        example = {
                            # 'position': f"{chrom}:{pos}",
                            'ref_alt': f"{ref}>{alt}",
                            'type': var_type,
                            'quality': qual,
                            'filter': filter_val,
                            'key_annotations': self._extract_key_annotations(info_fields, vcf_type)
                        }
                        analysis['variant_examples'].append(example)

            analysis['total'] = variant_count

            # Process header information
            analysis['header_info'] = self._parse_vcf_header(header_lines)

            # Calculate summary statistics for sample depths
            for sample_name, sample_data in analysis['sample_details'].items():
                if sample_data['depths']:
                    depths = sample_data['depths']
                    sample_data['depth_stats'] = {
                        'mean': round(sum(depths) / len(depths), 1),
                        'median': sorted(depths)[len(depths)//2],
                        'min': min(depths),
                        'max': max(depths)
                    }
                    sample_data['depths'] = []  # Clear to save memory

        except Exception as e:
            print(f"Error in VCF analysis for {vcf_path}: {e}")
            analysis['error'] = str(e)

        return analysis

    def _classify_variant_type(self, ref: str, alt: str) -> str:
        """Classify variant type based on REF and ALT."""
        if len(ref) == 1 and len(alt) == 1:
            return 'SNV'
        elif len(ref) == len(alt):
            return 'MNV'
        elif len(ref) < len(alt):
            return 'INS'
        elif len(ref) > len(alt):
            return 'DEL'
        else:
            return 'COMPLEX'

    def _extract_annotation_details(self, info_fields: dict, annotation_details: dict):
        """Extract annotation details from INFO field."""
        # Common annotation fields to track
        annotation_keys = [
            'CSQ', 'ANN', 'SYMBOL', 'Consequence', 'IMPACT', 'Gene',
            'Feature_type', 'BIOTYPE', 'HGVSc', 'HGVSp', 'TIER',
            'CLASSIFICATION', 'CPSR_CLASSIFICATION', 'PCGR_TIER'
        ]

        for key in annotation_keys:
            if key in info_fields:
                value = str(info_fields[key])
                if key not in annotation_details:
                    annotation_details[key] = {}

                # For complex annotations like CSQ, count unique values
                if key in ['CSQ', 'ANN'] and '|' in value:
                    # Split and count consequences
                    consequences = value.split(',')
                    for csq in consequences[:5]:  # Limit for performance
                        parts = csq.split('|')
                        if len(parts) > 1:
                            consequence = parts[1] if key == 'CSQ' else parts[2]
                            annotation_details[key][consequence] = \
                                annotation_details[key].get(consequence, 0) + 1
                else:
                    annotation_details[key][value] = annotation_details[key].get(value, 0) + 1

    def _extract_key_annotations(self, info_fields: dict, vcf_type: str) -> dict:
        """Extract key annotations for variant examples."""
        key_info = {}

        if vcf_type == 'pcgr_pass':
            for key in ['SYMBOL', 'Consequence', 'IMPACT', 'TIER', 'PCGR_TIER']:
                if key in info_fields:
                    key_info[key] = str(info_fields[key])[:50]  # Truncate long values
        elif vcf_type == 'cpsr_pass':
            for key in ['SYMBOL', 'Consequence', 'CLASSIFICATION', 'CPSR_CLASSIFICATION']:
                if key in info_fields:
                    key_info[key] = str(info_fields[key])[:50]
        else:
            # For other VCFs, extract general annotations
            for key in ['SVTYPE', 'SVLEN', 'IMPRECISE', 'END']:
                if key in info_fields:
                    key_info[key] = str(info_fields[key])[:50]

        return key_info

    def _parse_vcf_header(self, header_lines: list[str]) -> dict:
        """Parse VCF header for metadata."""
        header_info = {
            'format_lines': 0,
            'info_lines': 0,
            'filter_lines': 0,
            'contig_lines': 0,
            'software_versions': {},
            'reference_genome': None
        }

        for line in header_lines:
            if line.startswith('##FORMAT='):
                header_info['format_lines'] += 1
            elif line.startswith('##INFO='):
                header_info['info_lines'] += 1
            elif line.startswith('##FILTER='):
                header_info['filter_lines'] += 1
            elif line.startswith('##contig='):
                header_info['contig_lines'] += 1
            elif line.startswith('##reference='):
                header_info['reference_genome'] = line.split('=', 1)[1]
            elif '=' in line and any(tool in line.lower() for tool in ['sage', 'purple', 'pcgr', 'cpsr', 'gridss']):
                # Extract software version info
                parts = line.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].replace('##', '')
                    value = parts[1]
                    header_info['software_versions'][key] = value[:100]  # Truncate

        return header_info

    def explore_subdirectories(self) -> dict[str, Any]:
        """Explore and analyze subdirectory structure and key files."""
        directory_analysis = {}

        # Map of expected subdirectories and their purposes
        expected_dirs = {
            'purple': 'Purple variant processing and purity/ploidy analysis',
            'smlv_somatic': 'Small variant somatic calling',
            'smlv_germline': 'Small variant germline calling',
            'sv': 'Structural variant calling',
            'cancer_report': 'Cancer report generation',
            'smlv_somatic/report/pcgr': 'PCGR somatic variant annotation',
            'smlv_germline/report/cpsr': 'CPSR germline variant annotation',
            'linx': 'LINX structural variant analysis',
            'chord': 'CHORD homologous recombination deficiency',
            'multiqc': 'MultiQC quality control reports'
        }

        directory_analysis['expected_directories'] = {}
        directory_analysis['file_inventory'] = {}

        for dir_name, description in expected_dirs.items():
            dir_path = os.path.join(self.base_dir, dir_name)
            dir_exists = os.path.exists(dir_path)

            directory_analysis['expected_directories'][dir_name] = {
                'exists': dir_exists,
                'description': description,
                'path': dir_name
            }

            if dir_exists:
                # Count files by type in this directory
                file_counts = {}
                total_size = 0

                for root, dirs, files in os.walk(dir_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        file_ext = os.path.splitext(file)[1].lower()
                        if not file_ext:
                            file_ext = 'no_extension'

                        file_counts[file_ext] = file_counts.get(file_ext, 0) + 1
                        try:
                            total_size += os.path.getsize(file_path)
                        except OSError:
                            pass

                directory_analysis['expected_directories'][dir_name]['file_counts'] = file_counts
                directory_analysis['expected_directories'][dir_name]['total_size_mb'] = round(total_size / (1024 * 1024), 2)
                directory_analysis['expected_directories'][dir_name]['total_files'] = sum(file_counts.values())

        # Analyze PCGR subdirectory in detail
        pcgr_dir = os.path.join(self.base_dir, 'smlv_somatic', 'report', 'pcgr')
        if os.path.exists(pcgr_dir):
            directory_analysis['pcgr_detailed'] = self._analyze_pcgr_directory(pcgr_dir)

        # Analyze CPSR subdirectory in detail
        cpsr_dir = os.path.join(self.base_dir, 'smlv_germline', 'report', 'cpsr')
        if os.path.exists(cpsr_dir):
            directory_analysis['cpsr_detailed'] = self._analyze_cpsr_directory(cpsr_dir)

        return directory_analysis

    def _analyze_pcgr_directory(self, pcgr_dir: str) -> dict[str, Any]:
        """Detailed analysis of PCGR output directory."""
        pcgr_analysis = {}

        # Expected PCGR files
        expected_files = [
            f'{self.tumor}.pcgr_acmg.grch38.pass.vcf.gz',
            f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tiers.tsv',
            f'{self.tumor}.pcgr_acmg.grch38.json.gz',
            f'{self.tumor}.pcgr_acmg.grch38.html',
            f'{self.tumor}.pcgr_acmg.grch38.maf',
            f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tsv'
        ]

        pcgr_analysis['file_status'] = {}
        for file_name in expected_files:
            file_path = os.path.join(pcgr_dir, file_name)
            pcgr_analysis['file_status'][file_name] = {
                'exists': os.path.exists(file_path),
                'size_mb': round(os.path.getsize(file_path) / (1024 * 1024), 2) if os.path.exists(file_path) else 0
            }

        # Parse PCGR tier statistics - try primary name then fallback variants
        pcgr_tiers_candidates = [
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tiers.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.snvs_indels.tiers.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tsv'),
        ]
        pcgr_tiers_found = None
        for c in pcgr_tiers_candidates:
            if os.path.exists(c):
                pcgr_tiers_found = c
                break
        if pcgr_tiers_found:
            pcgr_analysis['tier_counts'] = self.parse_tier_file(pcgr_tiers_found)
            pcgr_analysis['tier_file_used'] = os.path.basename(pcgr_tiers_found)

        # Try to find PCGR mutational signatures (msigs) TSV and parse top signatures
        msigs_candidates = [
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.mutational_signatures.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.mutational_signatures.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.msigs.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.msigs.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.msigs.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.msigs.tsv')
        ]
        msigs_found = None
        for m in msigs_candidates:
            if os.path.exists(m):
                msigs_found = m
                break
        if msigs_found:
            pcgr_analysis['mutational_signatures'] = {
                'path': msigs_found,
                'md5': self._calculate_file_md5(msigs_found),
                'metrics': self.parse_pcgr_msigs(msigs_found, top_n=8)
            }

        return pcgr_analysis

    def _analyze_cpsr_directory(self, cpsr_dir: str) -> dict[str, Any]:
        """Detailed analysis of CPSR output directory."""
        cpsr_analysis = {}

        # Expected CPSR files
        expected_files = [
            f'{self.normal}.cpsr.grch38.pass.vcf.gz',
            f'{self.normal}.cpsr.grch38.snvs_indels.tiers.tsv',
            f'{self.normal}.cpsr.grch38.json.gz',
            f'{self.normal}.cpsr.grch38.html',
            f'{self.normal}.cpsr.grch38.snvs_indels.tsv'
        ]

        cpsr_analysis['file_status'] = {}
        for file_name in expected_files:
            file_path = os.path.join(cpsr_dir, file_name)
            cpsr_analysis['file_status'][file_name] = {
                'exists': os.path.exists(file_path),
                'size_mb': round(os.path.getsize(file_path) / (1024 * 1024), 2) if os.path.exists(file_path) else 0
            }

        # Parse CPSR classification statistics - try primary then alternative names
        cpsr_tiers_candidates = [
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.snvs_indels.tiers.tsv'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.classification.tsv.gz'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.snvs_indels.tsv'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.classification.tsv.gz')
        ]
        cpsr_tiers_found = None
        for c in cpsr_tiers_candidates:
            if os.path.exists(c):
                cpsr_tiers_found = c
                break
        if cpsr_tiers_found:
            cpsr_analysis['classification_counts'] = self.parse_cpsr_tier_counts(cpsr_tiers_found)
            cpsr_analysis['classification_file_used'] = os.path.basename(cpsr_tiers_found)

        return cpsr_analysis

    def parse_purple_purity(self) -> dict[str, Any]:
        """Parse Purple purity file using pandas."""
        path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.purity.tsv')
        df = read_tsv_file(path)
        if df.empty:
            return {}
        return df.iloc[0].to_dict()

    def parse_purple_drivers(self, driver_type: str) -> list[dict[str, Any]]:
        """Parse Purple driver catalog using pandas."""
        path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.driver.catalog.{driver_type}.tsv')
        df = read_tsv_file(path)
        if df.empty:
            return []
        return df.to_dict(orient='records')

    def parse_purple_qc(self) -> dict[str, Any]:
        """Parse Purple QC metrics using pandas."""
        path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.qc')
        df = read_tsv_file(path)
        if df.empty:
            return {}
        return df.iloc[0].to_dict()

    def parse_purple_cnv_somatic(self) -> dict[str, Any]:
        """Parse Purple somatic CNV segments using pandas."""
        path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.cnv.somatic.tsv')
        df = read_tsv_file(path)
        if df.empty:
            return {'total_segments': 0, 'by_type': {}, 'summary_stats': {}}

        if 'copyNumber' not in df.columns:
            return {'total_segments': len(df), 'by_type': {}, 'summary_stats': {}}

        cn = pd.to_numeric(df['copyNumber'], errors='coerce')

        def categorise(x):
            if pd.isna(x):
                return 'unknown'
            if x < 0.5:
                return 'homozygous_loss'
            if x < 1.5:
                return 'heterozygous_loss'
            if x < 2.5:
                return 'normal'
            if x < 4.5:
                return 'gain'
            return 'amplification'

        by_type = cn.apply(categorise).value_counts().to_dict()
        valid_cn = cn.dropna()
        summary = {}
        if len(valid_cn) > 0:
            summary = {
                'mean_cn': float(valid_cn.mean()),
                'median_cn': float(valid_cn.median()),
                'min_cn': float(valid_cn.min()),
                'max_cn': float(valid_cn.max())
            }

        return {'total_segments': len(df), 'by_type': by_type, 'summary_stats': summary}

    def parse_purple_cnv_gene(self) -> dict[str, Any]:
        """Parse Purple gene-level CNV using pandas."""
        path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.cnv.gene.tsv')
        df = read_tsv_file(path)
        if df.empty:
            return {'total_genes': 0, 'by_type': {}, 'key_cancer_genes': {}}

        key_cancer_genes = ['TP53', 'BRCA1', 'BRCA2', 'EGFR', 'KRAS', 'PIK3CA', 'PTEN', 'RB1', 'MYC', 'ERBB2']

        def categorise(x):
            if pd.isna(x):
                return 'unknown'
            if x < 0.5:
                return 'homozygous_loss'
            if x < 1.5:
                return 'heterozygous_loss'
            if x < 2.5:
                return 'normal'
            if x < 4.5:
                return 'gain'
            return 'amplification'

        by_type = {}
        key_gene_status = {}

        if 'copyNumber' in df.columns:
            cn = pd.to_numeric(df['copyNumber'], errors='coerce')
            by_type = cn.apply(categorise).value_counts().to_dict()

            if 'gene' in df.columns:
                for _, row in df[df['gene'].isin(key_cancer_genes)].iterrows():
                    g = row['gene']
                    c = pd.to_numeric(row.get('copyNumber'), errors='coerce')
                    key_gene_status[g] = {'copyNumber': float(c) if pd.notna(c) else None, 'type': categorise(c)}

        return {'total_genes': len(df), 'by_type': by_type, 'key_cancer_genes': key_gene_status}

    def _parse_af_file(self, af_path: str) -> dict[str, Any]:
        """Parse AF (allele frequency) file using pandas."""
        df = read_tsv_file(af_path)
        if df.empty:
            return {'total_variants': 0, 'af_distribution': {}, 'summary_stats': {}}

        # Find AF column (typically second column or named 'AF')
        af_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
        af_values = pd.to_numeric(df[af_col], errors='coerce').dropna()

        if len(af_values) == 0:
            return {'total_variants': 0, 'af_distribution': {}, 'summary_stats': {}}

        af_distribution = {
            'very_low': int((af_values < 0.05).sum()),
            'low': int(((af_values >= 0.05) & (af_values < 0.1)).sum()),
            'medium': int(((af_values >= 0.1) & (af_values < 0.3)).sum()),
            'high': int((af_values >= 0.3).sum()),
        }

        return {
            'total_variants': len(af_values),
            'af_distribution': af_distribution,
            'summary_stats': {
                'mean_af': float(af_values.mean()),
                'median_af': float(af_values.median()),
                'min_af': float(af_values.min()),
                'max_af': float(af_values.max()),
            },
        }

    def parse_af_tumor_keygenes(self) -> dict[str, Any]:
        """Parse AF tumor keygenes - CRITICAL for PAVE impact."""
        for path in [
            os.path.join(self.base_dir, 'cancer_report', 'af_tumor_keygenes.txt'),
            os.path.join(self.base_dir, 'smlv_somatic', 'report', 'af_tumor_keygenes.txt'),
        ]:
            if os.path.exists(path):
                return self._parse_af_file(path)
        return {'total_variants': 0, 'af_distribution': {}, 'summary_stats': {}}

    def parse_af_tumor_global(self) -> dict[str, Any]:
        """Parse AF tumor global - CRITICAL for PAVE impact."""
        for path in [
            os.path.join(self.base_dir, 'cancer_report', 'af_tumor.txt'),
            os.path.join(self.base_dir, 'smlv_somatic', 'report', 'af_tumor.txt'),
        ]:
            if os.path.exists(path):
                return self._parse_af_file(path)
        return {'total_variants': 0, 'af_distribution': {}, 'summary_stats': {}}

    def parse_variant_counts_json(self, json_path: str) -> dict[str, Any]:
        """Parse variant counts JSON for TMB/MSI metrics."""
        if not os.path.exists(json_path):
            return {}

        data = self._read_json_file(json_path)
        if not data:
            return {}

        # Extract TMB and MSI related metrics
        tmb_msi = {}

        # Look for TMB metrics
        if 'tmb' in data:
            tmb_msi['tmb'] = data['tmb']
        elif 'TMB' in data:
            tmb_msi['tmb'] = data['TMB']

        # Look for MSI metrics
        if 'msi' in data:
            tmb_msi['msi'] = data['msi']
        elif 'MSI' in data:
            tmb_msi['msi'] = data['MSI']

        # Extract variant counts that affect TMB calculation
        for key in ['total_variants', 'coding_variants', 'synonymous', 'nonsynonymous']:
            if key in data:
                tmb_msi[key] = data[key]

        return tmb_msi

    def parse_pcgr_msigs(self, msigs_path: str, top_n: int = 5) -> list[dict[str, Any]]:
        """Parse PCGR mutational signatures TSV and return top N signatures."""
        df = read_tsv_file(msigs_path)
        if df.empty:
            return []

        # Find signature and proportion columns
        sig_col = next((c for c in df.columns if c.lower() in ('signature_id', 'signature')), None)
        prop_col = next((c for c in df.columns if c.lower() in ('prop_signature', 'prop', 'proportion')), None)
        aeti_col = next((c for c in df.columns if 'aetiology' in c.lower()), None)

        if not sig_col or not prop_col:
            return []

        df = df[[c for c in [sig_col, prop_col, aeti_col] if c]].copy()
        df[prop_col] = pd.to_numeric(df[prop_col], errors='coerce').fillna(0.0)
        df = df.sort_values(prop_col, ascending=False).head(top_n)

        return [
            {'signature_id': row[sig_col], 'prop': row[prop_col], 'aetiology': row.get(aeti_col) if aeti_col else None}
            for _, row in df.iterrows()
        ]

    def analyze_baf_circos(self) -> dict[str, Any]:
        """Analyze BAF circos plot (uses purple_dir)."""
        baf_plot_path = os.path.join(self.base_dir, 'cancer_report', f'{self.tumor}.circos_baf.png')
        if not os.path.exists(baf_plot_path):
            # Try alternative locations
            baf_plot_path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.circos_baf.png')

        baf_analysis = {
            'path': baf_plot_path,
            'md5': self._calculate_file_md5(baf_plot_path) if os.path.exists(baf_plot_path) else None,
            'exists': os.path.exists(baf_plot_path)
        }

        return baf_analysis

    def analyze_linx_outputs(self) -> dict[str, Any]:
        """Analyze LINX outputs (takes purple_dir as input)."""
        linx_dir = os.path.join(self.base_dir, 'linx')

        linx_analysis = {
            'directory_exists': os.path.exists(linx_dir),
            'key_files': {}
        }

        if not os.path.exists(linx_dir):
            return linx_analysis

        # Key LINX files to track with content metrics only
        key_files = [
            f'{self.tumor}.linx.breakend.tsv',
            f'{self.tumor}.linx.clusters.tsv',
            f'{self.tumor}.linx.fusion.tsv',
            f'{self.tumor}.linx.links.tsv',
            f'{self.tumor}.linx.svs.tsv'
        ]

        for filename in key_files:
            file_path = os.path.join(linx_dir, filename)
            exists = os.path.exists(file_path)
            linx_analysis['key_files'][filename] = {
                'path': file_path,
                'md5': self._calculate_file_md5(file_path) if exists else None,
                'exists': exists,
                'record_count': len(read_tsv_file(file_path)) if exists else 0,
            }

        return linx_analysis

    def analyze_cancer_report(self) -> dict[str, Any]:
        """Analyze cancer report (groups purple_dir outputs)."""
        cancer_report_dir = os.path.join(self.base_dir, 'cancer_report')

        report_analysis = {
            'directory_exists': os.path.exists(cancer_report_dir),
            'report_files': {}
        }

        # Key cancer report files - focus on content tracking only
        report_files = [
            f'{self.tumor}_cancer_report.html',
            f'{self.tumor}.cancer_report.html',
            'cancer_report.html'
        ]

        for filename in report_files:
            file_path = os.path.join(cancer_report_dir, filename)
            if os.path.exists(file_path):
                report_analysis['report_files'][filename] = {
                    'path': file_path,
                    'md5': self._calculate_file_md5(file_path),
                    'exists': True
                }
                break  # Found the report

        # Check for supporting files affected by PAVE - content tracking only
        supporting_files = [
            f'{self.tumor}.cnv.prioritised.tsv',
            f'{self.tumor}.sv.prioritised.tsv',
            f'{self.tumor}.hrdscore.csv',
            'af_tumor_keygenes.txt',
            'af_tumor.txt'
        ]

        for filename in supporting_files:
            file_path = os.path.join(cancer_report_dir, filename)
            exists = os.path.exists(file_path)
            report_analysis['report_files'][filename] = {
                'path': file_path,
                'md5': self._calculate_file_md5(file_path) if exists else None,
                'exists': exists,
                'record_count': len(read_tsv_file(file_path)) if exists else 0,
            }

        return report_analysis

    def parse_cancer_report_table(self, base_filename: str) -> dict[str, Any]:
        """Parse structured cancer report tables (supports TSV/JSON sources)."""
        table_dirs = [
            os.path.join(self.base_dir, 'cancer_report', 'cancer_report_tables'),
            os.path.join(self.base_dir, 'cancer_report', 'cancer_report_tables', 'json'),
            os.path.join(self.base_dir, 'cancer_report')
        ]

        # Generate filename variants to try (with and without pair prefix)
        filename_variants = [
            base_filename,  # e.g., L2500643-qc_summary
            f'{self.tumor}__{self.normal}_{base_filename}',  # e.g., L2500643__L2500642_L2500643-qc_summary
            f'{self.tumor}_{self.normal}_{base_filename}',   # e.g., L2500643_L2500642_L2500643-qc_summary
        ]

        candidates = []
        for directory in table_dirs:
            for variant in filename_variants:
                candidates.append(os.path.join(directory, f'{variant}.tsv.gz'))
                candidates.append(os.path.join(directory, f'{variant}.tsv'))
                candidates.append(os.path.join(directory, f'{variant}.txt.gz'))
                candidates.append(os.path.join(directory, f'{variant}.txt'))
                candidates.append(os.path.join(directory, f'{variant}.json.gz'))
                candidates.append(os.path.join(directory, f'{variant}.json'))

        chosen_path = next((path for path in candidates if os.path.exists(path)), None)

        # If not found by exact pattern, try wildcard search (for files with run ID prefixes)
        if not chosen_path:
            import glob
            suffix = base_filename.split('-', 1)[-1] if '-' in base_filename else base_filename
            for directory in table_dirs:
                if os.path.exists(directory):
                    # Search for any file ending with the suffix pattern
                    for ext in ['.tsv.gz', '.tsv', '.txt.gz', '.txt', '.json.gz', '.json']:
                        pattern = os.path.join(directory, f'*-{suffix}{ext}')
                        matches = glob.glob(pattern)
                        if matches:
                            chosen_path = matches[0]  # Take first match
                            break
                if chosen_path:
                    break

        result: dict[str, Any] = {
            'path': chosen_path,
            'md5': self._calculate_file_md5(chosen_path) if chosen_path else None,
            'metrics': {}
        }

        if not chosen_path:
            return result

        metrics: dict[str, Any] = {}

        try:
            if chosen_path.endswith(('.tsv', '.tsv.gz', '.txt', '.txt.gz')):
                lines = self._read_text_file(chosen_path)
                if lines:
                    header = [col.strip() for col in lines[0].split('\t')]
                    metrics['_entry_count'] = 0
                    used_keys: dict[str, int] = {}
                    for idx, line in enumerate(lines[1:], start=1):
                        if not line.strip():
                            continue
                        parts = [part.strip() for part in line.split('\t')]
                        metrics['_entry_count'] += 1
                        identifier = parts[0] if parts else f'entry_{idx - 1}'
                        identifier = identifier.strip() or f'entry_{idx - 1}'
                        occurrence = used_keys.get(identifier, 0)
                        unique_identifier = identifier if occurrence == 0 else f"{identifier}#{occurrence + 1}"
                        used_keys[identifier] = occurrence + 1

                        if len(parts) > 1:
                            metrics[unique_identifier] = self._stringify_value(parts[1])
                        else:
                            metrics[unique_identifier] = ''

                        for col_idx, value in enumerate(parts[2:], start=2):
                            column_name = header[col_idx] if col_idx < len(header) else f'col_{col_idx}'
                            metrics[f"{unique_identifier}__{column_name}"] = self._stringify_value(value)
            else:
                data = self._read_json_file(chosen_path)
                if isinstance(data, dict):
                    metrics['_entry_count'] = len(data)
                    for key, value in data.items():
                        metrics[str(key)] = self._stringify_value(value)
                elif isinstance(data, list):
                    metrics['_entry_count'] = len(data)
                    used_keys: dict[str, int] = {}
                    key_fields = ['key', 'variable', 'name', 'title', 'id', 'label']

                    for idx, entry in enumerate(data):
                        if isinstance(entry, dict):
                            identifier = next((entry.get(field) for field in key_fields if entry.get(field)), None)
                            if identifier is None:
                                identifier = f'entry_{idx}'
                            identifier = str(identifier)

                            occurrence = used_keys.get(identifier, 0)
                            unique_identifier = identifier if occurrence == 0 else f"{identifier}#{occurrence + 1}"
                            used_keys[identifier] = occurrence + 1

                            if 'value' in entry:
                                metrics[unique_identifier] = self._stringify_value(entry.get('value'))

                            for extra_key, extra_value in entry.items():
                                if extra_key in key_fields or extra_key == 'value':
                                    continue
                                metrics[f"{unique_identifier}__{extra_key}"] = self._stringify_value(extra_value)
                        else:
                            metrics[f'entry_{idx}'] = self._stringify_value(entry)
                else:
                    metrics['_value'] = self._stringify_value(data)
        except Exception as e:
            print(f"Warning: could not normalise cancer report table {chosen_path}: {e}")

        result['metrics'] = metrics
        return result

    def parse_multiqc_data(self) -> dict[str, Any]:
        """Parse MultiQC data."""
        multiqc_path = os.path.join(self.base_dir, 'multiqc_data', 'multiqc_data.json')
        multiqc_data = self._read_json_file(multiqc_path)

        if not multiqc_data:
            # Try TSV version using pandas
            txt_path = os.path.join(self.base_dir, 'multiqc_data', 'multiqc_general_stats.txt')
            df = read_tsv_file(txt_path)
            if not df.empty:
                # Use first column as sample name, rest as metrics
                df = df.set_index(df.columns[0])
                return {'general_stats': df.to_dict(orient='index')}

        return multiqc_data

    def _parse_multiqc_tsv(self, path: str) -> dict[str, dict[str, Any]]:
        """Parse a generic MultiQC-style TSV into a nested dictionary using pandas."""
        df = read_tsv_file(path)
        if df.empty:
            return {}
        df = df.set_index(df.columns[0])
        return df.to_dict(orient='index')

    def parse_dragen_metrics(self) -> dict[str, Any]:
        """Collect DRAGEN-related MultiQC tables for downstream comparison."""
        multiqc_dir = os.path.join(self.base_dir, 'multiqc_data')
        if not os.path.exists(multiqc_dir):
            return {}

        dragen_files = {
            'dragen_map_metrics': 'dragen_map_metrics.txt',
            'dragen_vc_metrics': 'dragen_vc_metrics.txt',
            'dragen_time_metrics': 'dragen_time_metrics.txt',
            'dragen_wgs_cov_metrics': 'dragen_wgs_cov_metrics.txt',
            'dragen_tmb_coverage_metrics': 'dragen_tmb_coverage_metrics.txt',
            'dragen_trimmer_metrics': 'dragen_trimmer_metrics.txt',
            'dragen_frag_len': 'dragen_frag_len.txt',
            'dragen_ploidy': 'dragen_ploidy.txt'
        }

        dragen_metrics: dict[str, Any] = {}

        for key, filename in dragen_files.items():
            file_path = os.path.join(multiqc_dir, filename)
            if not os.path.exists(file_path):
                dragen_metrics[key] = {
                    'status': 'missing',
                    'path': file_path,
                    'md5': None,
                    'table': {}
                }
                continue

            dragen_metrics[key] = {
                'status': 'present',
                'path': file_path,
                'md5': self._calculate_file_md5(file_path),
                'table': self._parse_multiqc_tsv(file_path)
            }

        return dragen_metrics

    def analyze_run(self) -> dict[str, Any]:
        """Perform comprehensive analysis of the run - focused on metrics, not file sizes."""
        analysis = {
            'run_info': {
                'run_dir': self.run_dir,
                'base_dir': self.base_dir,
                'tumor': self.tumor,
                'normal': self.normal
            }
        }

        # BCFtools statistics - CONTENT COMPARISON
        somatic_stats_path = os.path.join(self.base_dir, 'smlv_somatic', 'report', f'{self.tumor}.somatic.bcftools_stats.txt')
        germline_stats_path = os.path.join(self.base_dir, 'smlv_germline', 'report', f'{self.normal}.germline.bcftools_stats.txt')

        analysis['somatic_bcftools'] = {
            'path': somatic_stats_path,
            'md5': self._calculate_file_md5(somatic_stats_path),
            'metrics': self.parse_bcftools_stats(somatic_stats_path)
        }
        analysis['germline_bcftools'] = {
            'path': germline_stats_path,
            'md5': self._calculate_file_md5(germline_stats_path),
            'metrics': self.parse_bcftools_stats(germline_stats_path)
        }

        # VCF analysis - CONTENT COMPARISON
        purple_somatic_vcf = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.somatic.vcf.gz')
        purple_sv_vcf = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.sv.vcf.gz')

        analysis['purple_somatic_vcf'] = {
            'path': purple_somatic_vcf,
            'md5': self._calculate_file_md5(purple_somatic_vcf),
            'metrics': self.count_vcf_variants(purple_somatic_vcf)
        }
        analysis['purple_sv_vcf'] = {
            'path': purple_sv_vcf,
            'md5': self._calculate_file_md5(purple_sv_vcf),
            'metrics': self.parse_sv_vcf(purple_sv_vcf)
        }

        # PCGR/CPSR analysis - CONTENT COMPARISON
        pcgr_dir = os.path.join(self.base_dir, 'smlv_somatic', 'report', 'pcgr')
        cpsr_dir = os.path.join(self.base_dir, 'smlv_germline', 'report', 'cpsr')

        # Find PCGR tiers file with fallbacks (including .pcgr.grch38.snv_indel_ann.tsv.gz)
        pcgr_tiers_candidates = [
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tiers.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.snvs_indels.tiers.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.snvs_indels.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.snv_indel_ann.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.snv_indel_ann.tsv')
        ]
        pcgr_tiers_found = None
        for c in pcgr_tiers_candidates:
            if os.path.exists(c):
                pcgr_tiers_found = c
                break
        if pcgr_tiers_found:
            analysis['pcgr_tiers'] = {
                'path': pcgr_tiers_found,
                'md5': self._calculate_file_md5(pcgr_tiers_found),
                'metrics': self.parse_tier_file(pcgr_tiers_found)
            }
        else:
            analysis['pcgr_tiers'] = {'path': None, 'md5': None, 'metrics': {}}

        # Also try to find PCGR msigs file for mutational signatures and include top signatures
        msigs_candidates = [
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.mutational_signatures.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.mutational_signatures.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.msigs.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.grch38.msigs.tsv'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.msigs.tsv.gz'),
            os.path.join(pcgr_dir, f'{self.tumor}.pcgr.msigs.tsv')
        ]
        msigs_found = None
        for m in msigs_candidates:
            if os.path.exists(m):
                msigs_found = m
                break
        if msigs_found:
            analysis['pcgr_mutational_signatures'] = {
                'path': msigs_found,
                'md5': self._calculate_file_md5(msigs_found),
                'metrics': self.parse_pcgr_msigs(msigs_found, top_n=8)
            }
        else:
            # No numeric msigs TSV found. Try to find human-readable MutSigs
            # summary in the cancer_report QC JSON as a fallback (qc_summary.json[.gz]).
            analysis['pcgr_mutational_signatures'] = {'path': None, 'md5': None, 'metrics': []}

            # Recursively search the cancer_report directory for qc_summary JSON(.gz) files
            cancer_report_dir = os.path.join(self.base_dir, 'cancer_report')
            qc_found = None
            if os.path.exists(cancer_report_dir):
                for root, dirs, files in os.walk(cancer_report_dir):
                    for fn in files:
                        if 'qc_summary' in fn.lower() and (fn.lower().endswith('.json') or fn.lower().endswith('.json.gz')):
                            qc_found = os.path.join(root, fn)
                            break
                    if qc_found:
                        break

            if qc_found:
                try:
                    # Load JSON content (handle gzipped)
                    if qc_found.endswith('.gz'):
                        with gzip.open(qc_found, 'rt', encoding='utf-8', errors='ignore') as fh:
                            qc_data = json.load(fh)
                    else:
                        qc_data = self._read_json_file(qc_found)

                    human_summary = {}
                    # qc_data is often a list of dicts with 'title' and 'value' keys
                    if isinstance(qc_data, list):
                        for item in qc_data:
                            if not isinstance(item, dict):
                                continue
                            title = item.get('title') or item.get('name') or item.get('label')
                            value = item.get('value') or item.get('text') or item.get('summary')
                            if title and isinstance(title, str):
                                ttl = title.lower()
                                if 'mutsigs' in ttl or ('mut' in ttl and 'sig' in ttl):
                                    human_summary[title] = value

                    elif isinstance(qc_data, dict):
                        for k, v in qc_data.items():
                            if isinstance(k, str):
                                kk = k.lower()
                                if 'mutsigs' in kk or ('mut' in kk and 'sig' in kk):
                                    human_summary[k] = v

                    if human_summary:
                        analysis['pcgr_mutational_signatures']['human_summary'] = human_summary
                        analysis['pcgr_mutational_signatures']['qc_summary_used'] = os.path.relpath(qc_found, self.base_dir)
                except Exception as e:
                    print(f"Warning: could not parse QC summary {qc_found}: {e}")

        # Find CPSR tiers/classification file with fallbacks (including .cpsr.grch38.classification.tsv.gz)
        cpsr_tiers_candidates = [
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.snvs_indels.tiers.tsv'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.classification.tsv.gz'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.snvs_indels.tsv'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.classification.tsv'),
            os.path.join(cpsr_dir, f'{self.normal}.cpsr.classification.tsv.gz')
        ]
        cpsr_tiers_found = None
        for c in cpsr_tiers_candidates:
            if os.path.exists(c):
                cpsr_tiers_found = c
                break
        if cpsr_tiers_found:
            analysis['cpsr_tiers'] = {
                'path': cpsr_tiers_found,
                'md5': self._calculate_file_md5(cpsr_tiers_found),
                'metrics': self.parse_cpsr_tier_counts(cpsr_tiers_found)
            }
        else:
            analysis['cpsr_tiers'] = {'path': None, 'md5': None, 'metrics': {}}

        # PCGR/CPSR VCF files for overlap analysis - CONTENT COMPARISON
        pcgr_pass_vcf = os.path.join(pcgr_dir, f'{self.tumor}.pcgr_acmg.grch38.pass.vcf.gz')
        _cpsr_pass_vcf = os.path.join(cpsr_dir, f'{self.normal}.cpsr.grch38.pass.vcf.gz')

        # Detailed PCGR PASS records for deep-dive
        analysis['pcgr_pass_records'] = self.parse_pcgr_pass_records(pcgr_pass_vcf)

        # Purple analysis - ENHANCED for PAVE impact tracking - CONTENT COMPARISON
        purity_path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.purity.tsv')
        qc_path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.qc')
        cnv_somatic_path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.cnv.somatic.tsv')
        cnv_gene_path = os.path.join(self.base_dir, 'purple', f'{self.tumor}.purple.cnv.gene.tsv')

        analysis['purple_purity'] = {
            'path': purity_path,
            'md5': self._calculate_file_md5(purity_path),
            'metrics': self.parse_purple_purity()
        }
        analysis['purple_qc'] = {
            'path': qc_path,
            'md5': self._calculate_file_md5(qc_path),
            'metrics': self.parse_purple_qc()
        }
        analysis['purple_cnv_somatic'] = {
            'path': cnv_somatic_path,
            'md5': self._calculate_file_md5(cnv_somatic_path),
            'metrics': self.parse_purple_cnv_somatic()
        }
        analysis['purple_cnv_gene'] = {
            'path': cnv_gene_path,
            'md5': self._calculate_file_md5(cnv_gene_path),
            'metrics': self.parse_purple_cnv_gene()
        }

        analysis['purple_drivers_somatic'] = self.parse_purple_drivers('somatic')
        analysis['purple_drivers_germline'] = self.parse_purple_drivers('germline')

        # AF tables analysis - CRITICAL for PAVE impact - CONTENT COMPARISON
        af_keygenes_path = os.path.join(self.base_dir, 'cancer_report', 'af_tumor_keygenes.txt')
        af_global_path = os.path.join(self.base_dir, 'cancer_report', 'af_tumor.txt')

        analysis['af_tumor_keygenes'] = {
            'path': af_keygenes_path,
            'md5': self._calculate_file_md5(af_keygenes_path),
            'metrics': self.parse_af_tumor_keygenes()
        }
        analysis['af_tumor_global'] = {
            'path': af_global_path,
            'md5': self._calculate_file_md5(af_global_path),
            'metrics': self.parse_af_tumor_global()
        }

        # Cancer report JSON tables (QC summary) - CONTENT COMPARISON
        qc_summary_basename = f'{self.tumor}-qc_summary'
        analysis['cancer_report_qc_summary'] = self.parse_cancer_report_table(qc_summary_basename)

        # TMB/MSI calculations - from variant counts JSON - CONTENT COMPARISON
        variant_counts_path = os.path.join(self.base_dir, 'smlv_somatic', 'report', f'{self.tumor}.somatic.variant_counts_process.json')
        analysis['tmb_msi_metrics'] = {
            'path': variant_counts_path,
            'md5': self._calculate_file_md5(variant_counts_path),
            'metrics': self.parse_variant_counts_json(variant_counts_path)
        }

        # SV pipeline steps - COUNT ONLY WHEN FILES DIFFER
        sv_dir = os.path.join(self.base_dir, 'sv_somatic')
        annotated_sv_path = os.path.join(sv_dir, 'annotate', f'{self.tumor}.annotated.vcf.gz')
        sv_prioritised_path = os.path.join(sv_dir, 'prioritise', f'{self.tumor}.sv.prioritised.tsv')
        cnv_prioritised_path = os.path.join(sv_dir, 'prioritise', f'{self.tumor}.cnv.prioritised.tsv')

        def _count_data_lines(path: str, skip_header: bool = True) -> int:
            """Count data lines in a file (VCF: skip # lines, TSV: skip header)."""
            lines = self._read_text_file(path)
            if not lines:
                return 0
            if skip_header:
                return sum(1 for line in lines if not line.startswith('#')) - 1  # TSV header
            return len([line for line in lines if not line.startswith('#')])

        analysis['sv_pipeline_steps'] = {
            'annotated_sv': {
                'path': annotated_sv_path,
                'md5': self._calculate_file_md5(annotated_sv_path),
                'count': _count_data_lines(annotated_sv_path, skip_header=False)
            },
            'sv_prioritised': {
                'path': sv_prioritised_path,
                'md5': self._calculate_file_md5(sv_prioritised_path),
                'count': max(0, len(self._read_text_file(sv_prioritised_path)) - 1)
            },
            'cnv_prioritised': {
                'path': cnv_prioritised_path,
                'md5': self._calculate_file_md5(cnv_prioritised_path),
                'count': max(0, len(self._read_text_file(cnv_prioritised_path)) - 1)
            }
        }

        # BAF circos plot analysis - CONTENT COMPARISON
        analysis['baf_circos'] = self.analyze_baf_circos()

        # MultiQC analysis - CONTENT COMPARISON
        analysis['multiqc'] = self.parse_multiqc_data()

        # DRAGEN MultiQC tables - CONTENT COMPARISON
        analysis['dragen_metrics'] = self.parse_dragen_metrics()

        # LINX analysis - CONTENT COMPARISON
        analysis['linx_analysis'] = self.analyze_linx_outputs()

        # Cancer report analysis - CONTENT COMPARISON
        analysis['cancer_report'] = self.analyze_cancer_report()

        return analysis

    def parse_pcgr_pass_records(self, vcf_path: str) -> list[dict[str, Any]]:
        """Parse PCGR PASS VCF into detailed per-allele records.

        Extracts: CHROM, POS, REF, ALT, key, var_type, TUMOR_AF/DP, NORMAL_AF/DP,
        and selected VEP (CSQ) fields for the matching allele (PICK=1 or CANONICAL).
        """
        lines = self._read_text_file(vcf_path)
        if not lines:
            return []

        # Parse CSQ header to get field order
        csq_fields: list[str] = []
        for h in lines:
            if h.startswith('##INFO=<ID=CSQ') and 'Format:' in h:
                m = re.search(r'Format: ([^">]+)', h)
                if m:
                    csq_fields = m.group(1).split('|')
                break

        def parse_info(info_str: str) -> dict[str, Any]:
            d = {}
            for item in info_str.split(';'):
                if '=' in item:
                    k, v = item.split('=', 1)
                    d[k] = v
                else:
                    d[item] = True
            return d

        def classify_variant(ref: str, alt: str) -> str:
            if len(ref) == 1 and len(alt) == 1:
                return 'SNV'
            if len(ref) == len(alt) and len(ref) > 1:
                return 'MNP'
            return 'Insertion' if len(alt) > len(ref) else 'Deletion'

        def best_csq(csq_entries: list[str], alt: str) -> dict[str, Any]:
            if not csq_entries or not csq_fields:
                return {}
            # Filter to allele-specific entries
            alle_idx = csq_fields.index('Allele') if 'Allele' in csq_fields else 0
            entries = [e for e in csq_entries if (e.split('|')[alle_idx] if len(e.split('|')) > alle_idx else '') == alt]
            if not entries:
                entries = csq_entries
            def entry_to_dict(e: str) -> dict[str, Any]:
                parts = e.split('|')
                return {csq_fields[i]: (parts[i] if i < len(parts) else '') for i in range(len(csq_fields))}
            dicts = [entry_to_dict(e) for e in entries]
            # Prefer PICK=1 then CANONICAL=YES
            for d in dicts:
                if d.get('PICK') == '1':
                    return d
            for d in dicts:
                if d.get('CANONICAL') in ('YES', 'Yes', '1'):
                    return d
            return dicts[0]

        records: list[dict[str, Any]] = []
        for line in lines:
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 8:
                continue
            chrom, pos, _id, ref, alts, _qual, _flt, info = parts[:8]
            pos_i = int(pos)
            info_d = parse_info(info)
            csq_val = info_d.get('CSQ', '')
            csq_entries = csq_val.split(',') if csq_val else []
            tumor_af = float(info_d.get('TUMOR_AF', 'nan')) if 'TUMOR_AF' in info_d else None
            tumor_dp = int(info_d.get('TUMOR_DP')) if 'TUMOR_DP' in info_d and str(info_d.get('TUMOR_DP')).isdigit() else None
            normal_af = float(info_d.get('NORMAL_AF', 'nan')) if 'NORMAL_AF' in info_d else None
            normal_dp = int(info_d.get('NORMAL_DP')) if 'NORMAL_DP' in info_d and str(info_d.get('NORMAL_DP')).isdigit() else None
            for alt in alts.split(','):
                key = f"{chrom}:{pos}:{ref}:{alt}"
                vt = classify_variant(ref, alt)
                csq = best_csq(csq_entries, alt)
                rec = {
                    'CHROM': chrom,
                    'POS': pos_i,
                    'REF': ref,
                    'ALT': alt,
                    'key': key,
                    'var_type': vt,
                    'TUMOR_AF': tumor_af,
                    'TUMOR_DP': tumor_dp,
                    'NORMAL_AF': normal_af,
                    'NORMAL_DP': normal_dp,
                    'SYMBOL': csq.get('SYMBOL'),
                    'Gene': csq.get('Gene'),
                    'Consequence': csq.get('Consequence'),
                    'IMPACT': csq.get('IMPACT'),
                    'gnomAD_AF': (float(csq.get('gnomAD_AF')) if (csq.get('gnomAD_AF') and csq.get('gnomAD_AF') not in ('', '.')) else None),
                    'HGVSp': csq.get('HGVSp'),
                    'HGVSc': csq.get('HGVSc'),
                }
                records.append(rec)
        return records


class ComparisonReporter:
    """Generate comprehensive comparison reports."""

    def __init__(self, analysis1: dict[str, Any], analysis2: dict[str, Any],
                 run1_name: str, run2_name: str):
        self.analysis1 = analysis1
        self.analysis2 = analysis2
        self.run1_name = run1_name
        self.run2_name = run2_name

    def calculate_jaccard_similarity(self, set1: set[str], set2: set[str]) -> float:
        """Calculate Jaccard similarity between two sets."""
        if not set1 and not set2:
            return 1.0
        if not set1 or not set2:
            return 0.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    def format_number(self, value) -> str:
        """Format number for display."""
        if value is None:
            return "N/A"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    def _safe_str(self, value) -> str:
        """Return a readable string representation, normalising missing values."""
        if value is None:
            return 'N/A'
        if isinstance(value, dict):
            if not value:
                return 'N/A'
            ordered = sorted(value.items(), key=lambda item: str(item[0]))
            return "; ".join(f"{k}: {v}" for k, v in ordered)
        if isinstance(value, (list, tuple, set)):
            if not value:
                return 'N/A'
            return ", ".join(str(item) for item in value)
        value_str = str(value).strip()
        return value_str if value_str else 'N/A'

    def _parse_numeric(self, value) -> float | None:
        """Attempt to convert a value to float, handling commas and percentages."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text:
            return None

        # Remove thousands separators and percentage signs
        text = text.replace(',', '')
        text = text.removesuffix('%')

        try:
            return float(text)
        except ValueError:
            return None

    def _format_numeric_diff(self, diff: float) -> str:
        """Format a numeric delta with adaptive precision."""
        if abs(diff) >= 1000:
            return f"{diff:+,.0f}"
        if abs(diff) >= 10:
            return f"{diff:+,.1f}"
        if abs(diff) >= 1:
            return f"{diff:+,.2f}"
        return f"{diff:+,.3f}"

    def _chrom_sort_key(self, chrom: str):
        """Sort chromosomes in natural genomic order."""
        chrom_str = str(chrom).replace('chr', '').replace('CHR', '')
        chrom_lower = chrom_str.lower()
        special = {'x': 23, 'y': 24, 'm': 25, 'mt': 25}

        if chrom_lower in special:
            return (0, special[chrom_lower])

        try:
            return (0, int(chrom_str))
        except ValueError:
            return (1, chrom_lower)

    def _metric_sort_key(self, key: Any):
        """Provide a natural sort key for metric columns."""
        if isinstance(key, (int, float)):
            return (0, float(key))

        text = str(key).strip()
        try:
            return (0, float(text))
        except ValueError:
            return (1, text.lower())

    def _format_counter_table(self, title: str, counter1: dict[str, Any],
                              counter2: dict[str, Any], sort_key=None,
                              indent: int = 2) -> list[str]:
        """Format a side-by-side comparison table for counter-style dictionaries."""
        all_keys = set(counter1.keys()) | set(counter2.keys())
        if not all_keys:
            return []

        indent_str = ' ' * indent
        key_width = max(10, max(len(str(k)) for k in all_keys) + 2)
        value_width = max(12, len(self.run1_name) + 4, len(self.run2_name) + 4)
        header = (f"{indent_str}{'Key':<{key_width}} {self.run1_name:<{value_width}} "
                  f"{self.run2_name:<{value_width}} {'Delta':<12}")

        lines = [f"{indent_str}{title}:", header,
                 f"{indent_str}" + "-" * (len(header) - len(indent_str))]

        sorter = sort_key if sort_key else (lambda item: str(item))
        for key in sorted(all_keys, key=sorter):
            val1 = counter1.get(key, 0)
            val2 = counter2.get(key, 0)
            num1 = self._parse_numeric(val1)
            num2 = self._parse_numeric(val2)
            diff_value = 0.0
            if num1 is not None or num2 is not None:
                diff_value = (num2 if num2 is not None else 0.0) - (num1 if num1 is not None else 0.0)
            marker = " [CHANGED]" if diff_value != 0 else ""
            val1_str = self._safe_str(val1)
            val2_str = self._safe_str(val2)
            diff_str = self._format_numeric_diff(diff_value) if diff_value != 0 else "+0"
            lines.append(
                f"{indent_str}{key!s:<{key_width}} {val1_str:<{value_width}} "
                f"{val2_str:<{value_width}} {diff_str:<12}{marker}"
            )

        return lines

    def _format_cancer_qc_summary(self, metrics1: dict[str, Any],
                                  metrics2: dict[str, Any]) -> list[str]:
        """Produce a compact, readable summary for cancer QC metrics."""
        lines: list[str] = []

        count1 = metrics1.get('_entry_count')
        count2 = metrics2.get('_entry_count')
        if count1 is not None or count2 is not None:
            lines.append(f"Entries captured: {self._safe_str(count1)} vs {self._safe_str(count2)}")
            lines.append("")

        base_keys = sorted({
            key for key in set(metrics1.keys()) | set(metrics2.keys())
            if not key.startswith('_') and not key.endswith('__details')
        }, key=self._metric_sort_key)

        for key in base_keys:
            val1 = metrics1.get(key)
            val2 = metrics2.get(key)
            detail_key = f"{key}__details"
            detail1 = metrics1.get(detail_key)
            detail2 = metrics2.get(detail_key)

            lines.append(f"{key}:")
            if val1 == val2:
                lines.append(f"  Shared: {self._safe_str(val1)}")
            else:
                lines.append(f"  {self.run1_name}: {self._safe_str(val1)}")
                lines.append(f"  {self.run2_name}: {self._safe_str(val2)} [CHANGED]")

            if detail1 or detail2:
                if detail1 == detail2:
                    if detail1 and detail1 not in ('N/A', 'n/a', 'na', ''):
                        lines.append(f"    Details: {self._safe_str(detail1)}")
                else:
                    if detail1 and detail1 not in ('N/A', 'n/a', 'na', ''):
                        lines.append(f"    {self.run1_name} details: {self._safe_str(detail1)}")
                    if detail2 and detail2 not in ('N/A', 'n/a', 'na', ''):
                        lines.append(f"    {self.run2_name} details: {self._safe_str(detail2)} [CHANGED]")

            lines.append("")

        # Trim trailing blank line if present
        if lines and lines[-1] == "":
            lines.pop()

        return lines

    def _summarize_metric_changes(self, metrics1: dict[str, Any],
                                  metrics2: dict[str, Any],
                                  limit: int = 8) -> list[str]:
        """Summarize key metric differences between two samples."""
        all_metrics = set(metrics1.keys()) | set(metrics2.keys())
        diffs = []

        for metric in all_metrics:
            val1 = metrics1.get(metric)
            val2 = metrics2.get(metric)
            if val1 == val2:
                continue

            num1 = self._parse_numeric(val1)
            num2 = self._parse_numeric(val2)

            if num1 is not None and num2 is not None:
                delta = num2 - num1
                if abs(delta) < 1e-6:
                    continue
                diffs.append(('numeric', abs(delta), metric, val1, val2, delta))
            else:
                diffs.append(('text', 0, metric, val1, val2, None))

        if not diffs:
            return ["    No metric changes detected"]

        numeric = [d for d in diffs if d[0] == 'numeric']
        text = [d for d in diffs if d[0] == 'text']
        numeric.sort(key=lambda item: item[1], reverse=True)

        lines: list[str] = []
        for entry in numeric[:limit]:
            _, _, metric, val1, val2, delta = entry
            val1_str = self._safe_str(val1)
            val2_str = self._safe_str(val2)
            lines.append(
                f"    {metric}: {val1_str} -> {val2_str} "
                f"({self._format_numeric_diff(delta)})"
            )

        remaining = limit - len(lines)
        if remaining > 0:
            for entry in text[:remaining]:
                _, _, metric, val1, val2, _ = entry
                lines.append(
                    f"    {metric}: '{self._safe_str(val1)}' -> '{self._safe_str(val2)}'"
                )

        hidden = len(diffs) - len(lines)
        if hidden > 0:
            lines.append(f"    ... {hidden} additional metrics changed")

        return lines

    def compare_dict_metrics(self, dict1: dict, dict2: dict, title: str) -> list[str]:
        """Compare two dictionaries and return formatted comparison."""
        lines = [f"\n{title}"]
        lines.append("=" * len(title))
        lines.append("")  # Add space before table

        all_keys = set(dict1.keys()) | set(dict2.keys())

        if not all_keys:
            lines.append("   No metrics found in either run")
            return lines

        # Determine column widths dynamically
        max_key_len = max(len(str(key)) for key in all_keys)
        key_width = max(25, max_key_len + 2)
        value_width = max(15, len(self.run1_name) + 2, len(self.run2_name) + 2)

        # Table header with better formatting
        header = f"{'Metric':<{key_width}} {self.run1_name:<{value_width}} {self.run2_name:<{value_width}} {'Change':<12} {'Ratio':<8}"
        lines.append(header)
        lines.append("-" * len(header))

        # Group metrics by type for better readability
        numeric_metrics = []
        string_metrics = []

        for key in sorted(all_keys, key=self._metric_sort_key):
            val1 = dict1.get(key)
            val2 = dict2.get(key)

            if isinstance(val1, (int, float)) or isinstance(val2, (int, float)):
                numeric_metrics.append((key, val1, val2))
            else:
                string_metrics.append((key, val1, val2))

        # Display numeric metrics first (they're usually more important)
        for key, val1, val2 in numeric_metrics:
            diff = "-"
            ratio = "-"

            if val1 is not None and val2 is not None:
                try:
                    if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
                        diff = val2 - val1
                        ratio = val2 / val1 if val1 != 0 else "inf"

                        # Format difference with appropriate precision
                        if isinstance(diff, float) and abs(diff) < 0.001 or isinstance(diff, float):
                            diff_str = f"{diff:+.3f}"
                        else:
                            diff_str = f"{diff:+d}"

                        # Format ratio
                        if isinstance(ratio, float) and ratio != float('inf'):
                            ratio_str = f"{ratio:.3f}"
                        else:
                            ratio_str = str(ratio)

                        # Color coding for significant changes
                        if isinstance(diff, (int, float)) and diff != 0 and abs(diff) > (abs(val1) * 0.05 if val1 != 0 else 0):  # >5% change
                            diff_str = f"[CHANGED] {diff_str}"

                        diff = diff_str
                        ratio = ratio_str
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            lines.append(f"{key:<{key_width}} {self.format_number(val1):<{value_width}} {self.format_number(val2):<{value_width}} {diff:<12} {ratio:<8}")

        # Add separator if both types exist
        if numeric_metrics and string_metrics:
            lines.append("-" * len(header))

        # Display string metrics
        for key, val1, val2 in string_metrics:
            change_indicator = "=" if val1 == val2 else "[CHANGED]"
            val1_str = self._safe_str(val1)
            val2_str = self._safe_str(val2)
            lines.append(f"{key:<{key_width}} {val1_str:<{value_width}} {val2_str:<{value_width}} {change_indicator:<12} {'-':<8}")

        return lines

    def generate_overlap_analysis(self) -> list[str]:
        """Generate variant overlap analysis."""
        lines = ["\nVariant Overlap Analysis"]
        lines.append("=" * 24)

        # PCGR PASS variants overlap
        pcgr1 = self.analysis1.get('pcgr_pass_variants', set())
        pcgr2 = self.analysis2.get('pcgr_pass_variants', set())

        if pcgr1 or pcgr2:
            jaccard = self.calculate_jaccard_similarity(pcgr1, pcgr2)
            intersection = len(pcgr1 & pcgr2)
            only_run1 = len(pcgr1 - pcgr2)
            only_run2 = len(pcgr2 - pcgr1)

            lines.append("\nPCGR PASS Variants:")
            lines.append(f"  {self.run1_name} total: {len(pcgr1)}")
            lines.append(f"  {self.run2_name} total: {len(pcgr2)}")
            lines.append(f"  Shared: {intersection}")
            lines.append(f"  Only in {self.run1_name}: {only_run1}")
            lines.append(f"  Only in {self.run2_name}: {only_run2}")
            lines.append(f"  Jaccard similarity: {jaccard:.3f}")

        # CPSR PASS variants overlap
        cpsr1 = self.analysis1.get('cpsr_pass_variants', set())
        cpsr2 = self.analysis2.get('cpsr_pass_variants', set())

        if cpsr1 or cpsr2:
            jaccard = self.calculate_jaccard_similarity(cpsr1, cpsr2)
            intersection = len(cpsr1 & cpsr2)
            only_run1 = len(cpsr1 - cpsr2)
            only_run2 = len(cpsr2 - cpsr1)

            lines.append("\nCPSR PASS Variants:")
            lines.append(f"  {self.run1_name} total: {len(cpsr1)}")
            lines.append(f"  {self.run2_name} total: {len(cpsr2)}")
            lines.append(f"  Shared: {intersection}")
            lines.append(f"  Only in {self.run1_name}: {only_run1}")
            lines.append(f"  Only in {self.run2_name}: {only_run2}")
            lines.append(f"  Jaccard similarity: {jaccard:.3f}")

        return lines

    def investigate_pcgr_differences(self) -> list[str]:
        """Deep-dive into PCGR PASS differences to suggest likely causes.

        - Categorizes unique variants (by var_type, gene, consequence, impact)
        - Estimates MNP<->SNV splitting by neighborhood checks
        - Summarizes AF distributions for unique sets
        """
        lines = ["\nPCGR PASS Difference Investigation", "=" * 34]

        recs1 = {r['key']: r for r in (self.analysis1.get('pcgr_pass_records') or [])}
        recs2 = {r['key']: r for r in (self.analysis2.get('pcgr_pass_records') or [])}
        set1 = set(recs1.keys())
        set2 = set(recs2.keys())
        only1 = set1 - set2
        only2 = set2 - set1

        lines.append(f"Run 1 unique variants: {len(only1)}")
        lines.append(f"Run 2 unique variants: {len(only2)}")

        def summarize_unique(keys, recs, title):
            lines.append(f"\n{title}")
            lines.append("-" * len(title))
            from collections import Counter
            by_type = Counter()
            by_gene = Counter()
            by_cons = Counter()
            by_impact = Counter()
            chrom = Counter()
            afs = []
            for k in keys:
                r = recs.get(k)
                if not r:
                    continue
                by_type[r.get('var_type') or 'NA'] += 1
                by_gene[r.get('SYMBOL') or 'NA'] += 1
                by_cons[r.get('Consequence') or 'NA'] += 1
                by_impact[r.get('IMPACT') or 'NA'] += 1
                chrom[r.get('CHROM') or 'NA'] += 1
                af = r.get('TUMOR_AF')
                if isinstance(af, float):
                    afs.append(af)
            def top(counter, n=10):
                return ", ".join(f"{k}:{v}" for k, v in counter.most_common(n)) if counter else "(none)"
            lines.append(f"By type: {top(by_type)}")
            lines.append(f"Top genes: {top(by_gene)}")
            lines.append(f"Top consequences: {top(by_cons)}")
            lines.append(f"By impact: {top(by_impact)}")
            lines.append(f"By chromosome: {top(chrom)}")
            if afs:
                import numpy as np
                arr = np.array(afs)
                lines.append(f"Tumor AF: mean={arr.mean():.3f}, median={np.median(arr):.3f}, min={arr.min():.3f}, max={arr.max():.3f}")

        summarize_unique(only1, recs1, "Run 1 unique summary")
        summarize_unique(only2, recs2, "Run 2 unique summary")

        # Heuristic MNP<->SNV split detection
        def index_snvs(recs):
            idx = {}
            for r in recs.values():
                if r.get('var_type') == 'SNV':
                    idx.setdefault((r['CHROM'], r['POS']), []).append(r)
            return idx
        snv_idx1 = index_snvs(recs1)
        snv_idx2 = index_snvs(recs2)

        def is_mnp_split_in_other(mnp_rec, other_snv_idx):
            ref = mnp_rec['REF']
            alt = mnp_rec['ALT']
            L = len(ref)
            if len(ref) != len(alt) or L <= 1:
                return False
            chrom = mnp_rec['CHROM']
            pos = mnp_rec['POS']
            # check consecutive SNVs spanning the MNP region
            for i in range(L):
                p = pos + i
                snvs = other_snv_idx.get((chrom, p))
                if not snvs:
                    return False
            return True

        mnp_as_snvs_in_run2 = sum(1 for k in only1 if recs1.get(k, {}).get('var_type') == 'MNP' and is_mnp_split_in_other(recs1[k], snv_idx2))
        mnp_as_snvs_in_run1 = sum(1 for k in only2 if recs2.get(k, {}).get('var_type') == 'MNP' and is_mnp_split_in_other(recs2[k], snv_idx1))
        lines.append("\nMNP split heuristic:")
        lines.append(f"  Run 1-only MNPs that appear as SNV clusters in Run 2: {mnp_as_snvs_in_run2}")
        lines.append(f"  Run 2-only MNPs that appear as SNV clusters in Run 1: {mnp_as_snvs_in_run1}")

        return lines

    def generate_file_change_summary(self) -> list[str]:
        """Generate a summary of which files have changed based on MD5."""
        lines = ["\nFILE CHANGE SUMMARY (MD5-based)"]
        lines.append("=" * 37)
        lines.append("")

        changed_files = []
        identical_files = []
        missing_files = []

        # Key file categories to check - organized by analysis type
        file_categories = {
            "BCFtools Statistics": [
                ('somatic_bcftools', 'BCFtools Somatic Stats'),
                ('germline_bcftools', 'BCFtools Germline Stats')
            ],
            "Purple VCF Files": [
                ('purple_somatic_vcf', 'Purple Somatic VCF'),
                ('purple_sv_vcf', 'Purple SV VCF')
            ],
            "Purple Metrics": [
                ('purple_purity', 'Purple Purity'),
                ('purple_qc', 'Purple QC'),
                ('purple_cnv_somatic', 'Purple CNV Somatic'),
                ('purple_cnv_gene', 'Purple CNV Gene')
            ],
            "Tier Classifications": [
                ('pcgr_tiers', 'PCGR Tiers'),
                ('cpsr_tiers', 'CPSR Tiers')
            ],
            "Additional Metrics": [
                ('af_tumor_keygenes', 'AF Tumor Key Genes'),
                ('af_tumor_global', 'AF Tumor Global'),
                ('tmb_msi_metrics', 'TMB/MSI Metrics'),
                ('cancer_report_qc_summary', 'Cancer Report QC Summary')
            ]
        }

        # Process standard files
        for files in file_categories.values():
            for file_key, description in files:
                data1 = self.analysis1.get(file_key, {})
                data2 = self.analysis2.get(file_key, {})

                md5_1 = data1.get('md5')
                md5_2 = data2.get('md5')

                if md5_1 is None and md5_2 is None or md5_1 is None or md5_2 is None:
                    missing_files.append(description)
                elif md5_1 == md5_2:
                    identical_files.append(description)
                else:
                    changed_files.append(description)

        # SV pipeline files
        sv_steps = self.analysis1.get('sv_pipeline_steps', {})
        sv_steps2 = self.analysis2.get('sv_pipeline_steps', {})

        for step_name, step_desc in [('annotated_sv', 'SV Annotated'), ('sv_prioritised', 'SV Prioritised'), ('cnv_prioritised', 'CNV Prioritised')]:
            step1 = sv_steps.get(step_name, {})
            step2 = sv_steps2.get(step_name, {})

            md5_1 = step1.get('md5')
            md5_2 = step2.get('md5')

            if md5_1 is None and md5_2 is None or md5_1 is None or md5_2 is None:
                missing_files.append(step_desc)
            elif md5_1 == md5_2:
                identical_files.append(step_desc)
            else:
                changed_files.append(step_desc)

        # Create visual status overview
        lines.append("STATUS OVERVIEW")
        lines.append(f"  Changed Files:   {len(changed_files):>3} files")
        lines.append(f"  Identical Files: {len(identical_files):>3} files")
        lines.append(f"  Missing Files:   {len(missing_files):>3} files")
        lines.append("")

        # Detailed breakdown
        if changed_files:
            lines.append("CHANGED FILES (require detailed analysis):")
            lines.append("-" * 50)
            for i, file_desc in enumerate(changed_files, 1):
                lines.append(f"   {i:>2}. {file_desc}")
            lines.append("")

        if identical_files:
            lines.append("IDENTICAL FILES (no changes detected):")
            lines.append("-" * 43)
            for i, file_desc in enumerate(identical_files, 1):
                lines.append(f"   {i:>2}. {file_desc}")
            lines.append("")

        if missing_files:
            lines.append("MISSING FILES (not available for comparison):")
            lines.append("-" * 49)
            for i, file_desc in enumerate(missing_files, 1):
                lines.append(f"   {i:>2}. {file_desc}")
            lines.append("")

        lines.append("NOTE: Only files with different MD5 checksums undergo detailed content analysis.")
        lines.append("=" * 90)

        return lines

    def compare_content_with_md5_check(self, data1: dict[str, Any], data2: dict[str, Any],
                                       title: str, leading_break: bool = True) -> list[str]:
        """Compare content between two run data dicts."""
        first_line = f"{title}" if not leading_break else f"\n{title}"
        lines = [first_line]
        lines.append("=" * len(title))

        path1 = data1.get('path')
        path2 = data2.get('path')
        if path1 or path2:
            lines.append(f"{self.run1_name} path: {path1 or '(missing)'}")
            lines.append(f"{self.run2_name} path: {path2 or '(missing)'}")
            lines.append("")

        md5_1 = data1.get('md5')
        md5_2 = data2.get('md5')

        if md5_1 is None and md5_2 is None:
            lines.append("Files missing in both runs - no comparison possible")
            lines.append("")
            return lines
        if md5_1 is None:
            lines.append(f"File missing in {self.run1_name} - no comparison possible")
            lines.append("")
            return lines
        if md5_2 is None:
            lines.append(f"File missing in {self.run2_name} - no comparison possible")
            lines.append("")
            return lines

        # Extract metrics and compare
        metrics1 = data1.get('metrics', {})
        metrics2 = data2.get('metrics', {})

        if not isinstance(metrics1, dict):
            metrics1 = {}
        if not isinstance(metrics2, dict):
            metrics2 = {}

        if not metrics1 and not metrics2:
            lines.append("   No metrics extracted from either file")
            lines.append("")
            return lines

        nested_keys = {'by_type', 'by_filter', 'by_chromosome', 'by_quality'}

        if 'Cancer Report QC Summary' in title:
            lines.extend(self._format_cancer_qc_summary(metrics1, metrics2))
            lines.append("")
            return lines

        flat_metrics1 = {
            k: v for k, v in metrics1.items()
            if k not in nested_keys and not k.endswith('__details')
        }
        flat_metrics2 = {
            k: v for k, v in metrics2.items()
            if k not in nested_keys and not k.endswith('__details')
        }

        # Detailed metrics comparison
        detailed_title = f"{title} - Detailed Metrics" if not title.endswith("Detailed Metrics") else title
        lines.extend(self.compare_dict_metrics(flat_metrics1, flat_metrics2, detailed_title))

        # Expand common nested counters for readability
        counter_titles = {
            'by_type': 'Variant type distribution',
            'by_filter': 'Filter distribution',
            'by_chromosome': 'Chromosome distribution',
            'by_quality': 'Quality buckets'
        }

        for key in ['by_type', 'by_filter', 'by_chromosome', 'by_quality']:
            counter1 = metrics1.get(key, {}) if isinstance(metrics1.get(key), dict) else {}
            counter2 = metrics2.get(key, {}) if isinstance(metrics2.get(key), dict) else {}
            if not counter1 and not counter2:
                continue

            lines.append("")
            if key == 'by_chromosome':
                lines.extend(
                    self._format_counter_table(counter_titles[key], counter1, counter2,
                                               sort_key=self._chrom_sort_key, indent=4)
                )
            elif key == 'by_quality':
                order = ['high', 'medium', 'low', 'very_low']
                def sort_fn(item, _order=order):
                    return _order.index(item) if item in _order else len(_order)
                lines.extend(
                    self._format_counter_table(counter_titles[key], counter1, counter2,
                                               sort_key=sort_fn, indent=4)
                )
            else:
                lines.extend(
                    self._format_counter_table(counter_titles[key], counter1, counter2, indent=4)
                )

        detail_keys = sorted(
            k for k in set(metrics1.keys()) | set(metrics2.keys())
            if k.endswith('__details')
        )

        detail_lines: list[str] = []
        for detail_key in detail_keys:
            val1 = metrics1.get(detail_key)
            val2 = metrics2.get(detail_key)
            if val1 == val2:
                continue
            base_name = detail_key.removesuffix('__details')
            detail_lines.append(f"- {base_name} details differ:")
            if val1 is not None and val1 not in ('', 'N/A', 'n/a', 'na'):
                detail_lines.append(f"    {self.run1_name}: {self._safe_str(val1)}")
            if val2 is not None and val2 not in ('', 'N/A', 'n/a', 'na'):
                detail_lines.append(f"    {self.run2_name}: {self._safe_str(val2)} [CHANGED]")

        if detail_lines:
            lines.append("")
            lines.append("Detail differences:")
            lines.extend(detail_lines)

        lines.append("")
        return lines

    def compare_sv_pipeline_steps(self) -> list[str]:
        """Compare SV pipeline steps using MD5 checks first."""
        lines = ["\nSV Pipeline Steps"]
        lines.append("=" * 31)

        sv1 = self.analysis1.get('sv_pipeline_steps', {})
        sv2 = self.analysis2.get('sv_pipeline_steps', {})

        steps = ['annotated_sv', 'sv_prioritised', 'cnv_prioritised']
        step_names = ['Annotated SV VCF', 'SV Prioritised TSV', 'CNV Prioritised TSV']

        for step, step_name in zip(steps, step_names):
            step1 = sv1.get(step, {})
            step2 = sv2.get(step, {})

            md5_1 = step1.get('md5')
            md5_2 = step2.get('md5')
            count1 = step1.get('count', 0)
            count2 = step2.get('count', 0)

            lines.append(f"\n{step_name}:")

            if md5_1 is None and md5_2 is None:
                lines.append("  Files missing in both runs")
            elif md5_1 is None:
                lines.append(f"  File missing in {self.run1_name}")
                lines.append(f"  {self.run2_name}: {count2} records")
            elif md5_2 is None:
                lines.append(f"  File missing in {self.run2_name}")
                lines.append(f"  {self.run1_name}: {count1} records")
            else:
                lines.append(f"  {self.run1_name}: {count1} records")
                lines.append(f"  {self.run2_name}: {count2} records")
                diff = count2 - count1
                lines.append(f"  Difference: {diff:+d} records")

        return lines

    def compare_key_cancer_genes_cnv(self) -> list[str]:
        """Compare key cancer genes CNV status - CRITICAL for PAVE impact assessment."""
        lines = ["\nKey Cancer Genes CNV Status"]
        lines.append("=" * 42)

        cnv1 = self.analysis1.get('purple_cnv_gene', {}).get('key_cancer_genes', {})
        cnv2 = self.analysis2.get('purple_cnv_gene', {}).get('key_cancer_genes', {})

        all_genes = set(cnv1.keys()) | set(cnv2.keys())

        if not all_genes:
            lines.append("No key cancer genes found in either run")
            return lines

        lines.append(f"{'Gene':<10} {self.run1_name + ' CN':<15} {self.run1_name + ' Type':<15} {self.run2_name + ' CN':<15} {self.run2_name + ' Type':<15} {'Status':<15}")
        lines.append("-" * 90)

        for gene in sorted(all_genes):
            info1 = cnv1.get(gene, {})
            info2 = cnv2.get(gene, {})

            cn1 = info1.get('copyNumber', 'N/A')
            cn2 = info2.get('copyNumber', 'N/A')
            type1 = info1.get('type', 'N/A')
            type2 = info2.get('type', 'N/A')

            # Determine status
            if cn1 == 'N/A' and cn2 != 'N/A':
                status = f"New in {self.run2_name}"
            elif cn1 != 'N/A' and cn2 == 'N/A':
                status = f"Lost in {self.run2_name}"
            elif type1 != type2:
                status = "Type changed"
            elif cn1 != cn2 and cn1 != 'N/A' and cn2 != 'N/A':
                try:
                    diff = abs(float(cn2) - float(cn1))
                    if diff > 0.5:
                        status = "CN changed"
                    else:
                        status = "Stable"
                except (ValueError, TypeError):
                    status = "Stable"
            else:
                status = "Stable"

            cn1_str = f"{cn1:.2f}" if isinstance(cn1, float) else str(cn1)
            cn2_str = f"{cn2:.2f}" if isinstance(cn2, float) else str(cn2)

            lines.append(f"{gene:<10} {cn1_str:<15} {type1:<15} {cn2_str:<15} {type2:<15} {status:<15}")

        return lines

    def _format_dragen_heading(self, key: str) -> str:
        friendly = {
            'dragen_map_metrics': 'DRAGEN mapping metrics',
            'dragen_vc_metrics': 'DRAGEN variant calling metrics',
            'dragen_time_metrics': 'DRAGEN runtime metrics',
            'dragen_wgs_cov_metrics': 'DRAGEN WGS coverage metrics',
            'dragen_tmb_coverage_metrics': 'DRAGEN TMB coverage metrics',
            'dragen_trimmer_metrics': 'DRAGEN trimmer metrics',
            'dragen_frag_len': 'DRAGEN fragment length metrics',
            'dragen_ploidy': 'DRAGEN ploidy metrics'
        }
        return friendly.get(key, key.replace('_', ' ').title())

    def _compare_dragen_table(self, entry1: dict[str, Any], entry2: dict[str, Any]) -> list[str]:
        lines: list[str] = []

        path1 = entry1.get('path')
        path2 = entry2.get('path')
        lines.append(f"{self.run1_name} path: {path1 or '(missing)'}")
        lines.append(f"{self.run2_name} path: {path2 or '(missing)'}")

        md5_1 = entry1.get('md5')
        md5_2 = entry2.get('md5')

        if md5_1 is None and md5_2 is None:
            lines.append("Missing in both runs")
            return lines
        if md5_1 is None:
            lines.append(f"Missing in {self.run1_name}")
            return lines
        if md5_2 is None:
            lines.append(f"Missing in {self.run2_name}")
            return lines

        table1 = entry1.get('table') or {}
        table2 = entry2.get('table') or {}

        sample_names = sorted(set(table1.keys()) | set(table2.keys()))
        if not sample_names:
            lines.append("   No tabular metrics parsed from either file")
            return lines

        for sample in sample_names:
            sample_metrics1 = table1.get(sample)
            sample_metrics2 = table2.get(sample)
            lines.append(f"  Sample: {sample}")

            if sample_metrics1 is None and sample_metrics2 is None:
                lines.append("    No metrics present in either run")
                continue
            if sample_metrics1 is None:
                lines.append(f"    Present only in {self.run2_name}")
                continue
            if sample_metrics2 is None:
                lines.append(f"    Present only in {self.run1_name}")
                continue

            lines.extend(self._summarize_metric_changes(sample_metrics1, sample_metrics2))

        return lines

    def compare_dragen_metrics(self) -> list[str]:
        """Compare DRAGEN-specific MultiQC tables between runs."""
        lines = ["\nDRAGEN QC METRIC COMPARISON", "=" * 29]

        dragen1 = self.analysis1.get('dragen_metrics', {})
        dragen2 = self.analysis2.get('dragen_metrics', {})

        all_tables = sorted(set(dragen1.keys()) | set(dragen2.keys()))
        if not all_tables:
            lines.append("No DRAGEN metrics located in either run")
            return lines

        for table_key in all_tables:
            lines.append("")
            heading = self._format_dragen_heading(table_key)
            lines.append(heading.upper())
            lines.append("-" * len(heading))

            entry1 = dragen1.get(table_key, {})
            entry2 = dragen2.get(table_key, {})

            lines.extend(self._compare_dragen_table(entry1, entry2))

        return lines

    def exemplar_unique_variants(self) -> list[str]:
        """Pick one exemplar variant from each unique set and explain absence.

        Heuristics:
        - If Run 1 exemplar is an MNP and Run 2 contains matching consecutive SNVs
          at those bases, annotate as "split into SNVs in Run 2".
        - If Run 2 exemplar is an SNV covered by an MNP in Run 1 with matching
          base at the corresponding offset, annotate as "merged as MNP in Run 1".
        - Otherwise, report no matching allele in the other's PASS set.
        """
        lines = ["\nExemplar Unique Variants", "=" * 25]

        recs1 = {r['key']: r for r in (self.analysis1.get('pcgr_pass_records') or [])}
        recs2 = {r['key']: r for r in (self.analysis2.get('pcgr_pass_records') or [])}
        set1 = set(recs1.keys())
        set2 = set(recs2.keys())
        only1 = list(set1 - set2)
        only2 = list(set2 - set1)

        def snv_cluster_for_mnp(mnp_rec, other_recs):
            """Return the list of SNV records in other_recs that together match the MNP.

            Returns empty list if not all positions are represented as SNVs.
            """
            chrom = mnp_rec['CHROM']
            pos = mnp_rec['POS']
            ref = mnp_rec['REF']
            alt = mnp_rec['ALT']
            L = len(ref)
            if len(ref) != len(alt) or L <= 1:
                return []
            matches = []
            for i in range(L):
                k = f"{chrom}:{pos+i}:{ref[i]}:{alt[i]}"
                r = other_recs.get(k)
                if not r or r.get('var_type') != 'SNV':
                    return []
                matches.append(r)
            return matches

        def mnp_covering_snv(snv_rec, other_recs):
            chrom = snv_rec['CHROM']
            pos = snv_rec['POS']
            ref = snv_rec['REF']
            alt = snv_rec['ALT']
            # scan MNPs in other_recs to find one covering this base
            for r in other_recs.values():
                if r.get('var_type') != 'MNP' or r.get('CHROM') != chrom:
                    continue
                start = r['POS']
                L = len(r['REF'])
                if len(r['REF']) != len(r['ALT']) or L <= 1:
                    continue
                if start <= pos <= start + L - 1:
                    off = pos - start
                    if off < L and r['REF'][off] == ref and r['ALT'][off] == alt:
                        return r
            return None

        def fmt_variant(r):
            return (f"{r['CHROM']}:{r['POS']} {r['REF']}>{r['ALT']} | type={r.get('var_type')} | "
                    f"gene={r.get('SYMBOL') or 'NA'} | cons={r.get('Consequence') or 'NA'} | "
                    f"impact={r.get('IMPACT') or 'NA'} | AF={r.get('TUMOR_AF')} DP={r.get('TUMOR_DP')}")

        # Run 1 exemplar (prefer an MNP to showcase splitting)
        r1_ex = None
        for k in only1:
            if recs1.get(k, {}).get('var_type') == 'MNP':
                r1_ex = recs1[k]
                break
        if r1_ex is None and only1:
            r1_ex = recs1[only1[0]]

        if r1_ex:
            lines.append("\nRun 1 unique exemplar:")
            lines.append("- " + fmt_variant(r1_ex))
            explanation = "No matching allele in Run 2 PASS"
            if r1_ex.get('var_type') == 'MNP':
                matches = snv_cluster_for_mnp(r1_ex, recs2)
                if matches:
                    explanation = "Appears split into consecutive SNVs in Run 2"
                    # Print counterparts
                    lines.append("  Matching SNVs in Run 2:")
                    for mr in matches:
                        lines.append("    - " + fmt_variant(mr))
            else:
                # attempt reverse: if it's an SNV, does Run 2 have an MNP covering?
                if r1_ex.get('var_type') == 'SNV':
                    m = mnp_covering_snv(r1_ex, recs2)
                    if m:
                        explanation = ("Present as part of merged MNP in Run 2: "
                                       + f"{m['CHROM']}:{m['POS']} {m['REF']}>{m['ALT']}")
                        lines.append("  Matching MNP in Run 2:")
                        lines.append("    - " + fmt_variant(m))
            lines.append("  Explanation: " + explanation)

        # Run 2 exemplar (prefer an SNV to showcase merging in Run 1)
        r2_ex = None
        for k in only2:
            if recs2.get(k, {}).get('var_type') == 'SNV':
                r2_ex = recs2[k]
                break
        if r2_ex is None and only2:
            r2_ex = recs2[only2[0]]

        if r2_ex:
            lines.append("\nRun 2 unique exemplar:")
            lines.append("- " + fmt_variant(r2_ex))
            explanation = "No matching allele in Run 1 PASS"
            m = mnp_covering_snv(r2_ex, recs1)
            if m:
                explanation = ("Present as part of merged MNP in Run 1: "
                               + f"{m['CHROM']}:{m['POS']} {m['REF']}>{m['ALT']}")
                lines.append("  Matching MNP in Run 1:")
                lines.append("    - " + fmt_variant(m))
            else:
                # if it's an MNP in run2, check split SNVs in run1
                if r2_ex.get('var_type') == 'MNP':
                    matches = snv_cluster_for_mnp(r2_ex, recs1)
                    if matches:
                        explanation = "Appears split into consecutive SNVs in Run 1"
                        lines.append("  Matching SNVs in Run 1:")
                        for mr in matches:
                            lines.append("    - " + fmt_variant(mr))
            lines.append("  Explanation: " + explanation)

        return lines
    def extract_comparison_metrics(self) -> dict[str, Any]:
        """Extract structured metrics from both analyses for JSON export."""
        metrics = {
            'purple': {},
            'vcf_counts': {},
            'pcgr_tiers': {},
            'cpsr_tiers': {},
            'multiqc': {},
            'bcftools_stats': {},
            'sv_counts': {},
            'file_comparison': {}
        }

        # Purple purity and QC metrics
        for run_key, analysis in [('run1', self.analysis1), ('run2', self.analysis2)]:
            if 'purple_purity' in analysis and 'metrics' in analysis['purple_purity']:
                purity_data = analysis['purple_purity']['metrics']
                metrics['purple'][run_key] = {
                    'purity': purity_data.get('purity'),
                    'ploidy': purity_data.get('ploidy'),
                    'minPurity': purity_data.get('minPurity'),
                    'maxPurity': purity_data.get('maxPurity')
                }

            if 'purple_qc' in analysis and 'metrics' in analysis['purple_qc']:
                qc_data = analysis['purple_qc']['metrics']
                if run_key not in metrics['purple']:
                    metrics['purple'][run_key] = {}
                metrics['purple'][run_key].update({
                    'QCStatus': qc_data.get('QCStatus'),
                    'CopyNumberSegments': qc_data.get('CopyNumberSegments'),
                    'UnsupportedCopyNumberSegments': qc_data.get('UnsupportedCopyNumberSegments'),
                    'AmberMeanDepth': qc_data.get('AmberMeanDepth')
                })

        # VCF variant counts
        for run_key, analysis in [('run1', self.analysis1), ('run2', self.analysis2)]:
            metrics['vcf_counts'][run_key] = {}

            if 'purple_somatic_vcf' in analysis and 'metrics' in analysis['purple_somatic_vcf']:
                vcf_data = analysis['purple_somatic_vcf']['metrics']
                metrics['vcf_counts'][run_key]['purple_somatic'] = {
                    'total': vcf_data.get('total', 0),
                    'by_type': vcf_data.get('by_type', {}),
                    'by_filter': vcf_data.get('by_filter', {})
                }

            if 'purple_sv_vcf' in analysis and 'metrics' in analysis['purple_sv_vcf']:
                sv_data = analysis['purple_sv_vcf']['metrics']
                metrics['sv_counts'][run_key] = {
                    'total': sv_data.get('total', 0),
                    'by_type': sv_data.get('by_type', {}),
                    'by_filter': sv_data.get('by_filter', {})
                }

        # PCGR and CPSR tiers
        for run_key, analysis in [('run1', self.analysis1), ('run2', self.analysis2)]:
            if 'pcgr_tiers' in analysis and 'metrics' in analysis['pcgr_tiers']:
                metrics['pcgr_tiers'][run_key] = analysis['pcgr_tiers']['metrics']

            if 'cpsr_tiers' in analysis and 'metrics' in analysis['cpsr_tiers']:
                metrics['cpsr_tiers'][run_key] = analysis['cpsr_tiers']['metrics']

        # BCFtools statistics
        for run_key, analysis in [('run1', self.analysis1), ('run2', self.analysis2)]:
            if 'somatic_bcftools' in analysis and 'metrics' in analysis['somatic_bcftools']:
                metrics['bcftools_stats'][run_key] = {
                    'somatic': analysis['somatic_bcftools']['metrics']
                }
            if 'germline_bcftools' in analysis and 'metrics' in analysis['germline_bcftools']:
                if run_key not in metrics['bcftools_stats']:
                    metrics['bcftools_stats'][run_key] = {}
                metrics['bcftools_stats'][run_key]['germline'] = analysis['germline_bcftools']['metrics']

        # MultiQC summary stats (if available)
        for run_key, analysis in [('run1', self.analysis1), ('run2', self.analysis2)]:
            if 'multiqc_data' in analysis:
                multiqc = analysis['multiqc_data']
                if isinstance(multiqc, dict) and 'report_general_stats_data' in multiqc:
                    metrics['multiqc'][run_key] = multiqc['report_general_stats_data']

        # File comparison summary (counts of identical, different, missing)
        file_status = {
            'identical': 0,
            'different': 0,
            'different_keys': [],
            'missing_run1': 0,
            'missing_run2': 0,
            'missing_both': 0,
        }

        # Count file statuses from key comparisons
        for key in ['purple_purity', 'purple_qc', 'purple_somatic_vcf', 'purple_sv_vcf',
                    'pcgr_tiers', 'cpsr_tiers', 'somatic_bcftools', 'germline_bcftools']:
            if key in self.analysis1 and key in self.analysis2:
                md5_1 = self.analysis1[key].get('md5')
                md5_2 = self.analysis2[key].get('md5')

                if md5_1 is None and md5_2 is None:
                    file_status['missing_both'] += 1
                elif md5_1 is None:
                    file_status['missing_run1'] += 1
                elif md5_2 is None:
                    file_status['missing_run2'] += 1
                elif md5_1 == md5_2:
                    file_status['identical'] += 1
                else:
                    file_status['different'] += 1
                    file_status['different_keys'].append(key)

        metrics['file_comparison'] = file_status

        return metrics

    def generate_comprehensive_report(self) -> list[str]:
        """Generate the full comprehensive report - focused on content differences."""
        lines = []

        # Header
        lines.append("COMPREHENSIVE SASH RUN COMPARISON REPORT")
        lines.append("=" * 41)
        lines.append(f"{self.run1_name}: {self.analysis1['run_info']['run_dir']}")
        lines.append(f"{self.run2_name}: {self.analysis2['run_info']['run_dir']}")
        lines.append(f"Tumor Sample: {self.analysis1['run_info']['tumor']}")
        lines.append(f"Normal Sample: {self.analysis1['run_info']['normal']}")
        lines.append("")
        lines.append("ANALYSIS METHODOLOGY:")
        lines.append("=" * 21)
        lines.append("- Files are compared by content (metrics and values)")
        lines.append("- All files undergo detailed content analysis regardless of MD5 checksums")
        lines.append("- All comparisons focus on metrics and content, not file sizes")
        lines.append("")
        lines.append("=" * 90)
        lines.append("")

        # Highlight cancer report QC summary at the top of the report
        qc_section = self.compare_content_with_md5_check(
            self.analysis1.get('cancer_report_qc_summary', {}),
            self.analysis2.get('cancer_report_qc_summary', {}),
            "Cancer Report QC Summary - Detailed Metrics",
            leading_break=False
        )
        lines.extend(qc_section)
        lines.append("=" * 90)
        lines.append("")

        # BCFtools comparison
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('somatic_bcftools', {}),
            self.analysis2.get('somatic_bcftools', {}),
            "Somatic BCFtools Statistics"
        ))

        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('germline_bcftools', {}),
            self.analysis2.get('germline_bcftools', {}),
            "Germline BCFtools Statistics"
        ))

        # VCF variant analysis - only if files differ
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_somatic_vcf', {}),
            self.analysis2.get('purple_somatic_vcf', {}),
            "Purple Somatic VCF Analysis"
        ))

        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_sv_vcf', {}),
            self.analysis2.get('purple_sv_vcf', {}),
            "Purple SV VCF Analysis"
        ))

        # PCGR/CPSR tiers - only if files differ
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('pcgr_tiers', {}),
            self.analysis2.get('pcgr_tiers', {}),
            "PCGR Tier Distribution"
        ))

        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('cpsr_tiers', {}),
            self.analysis2.get('cpsr_tiers', {}),
            "CPSR Tier Distribution"
        ))

        # Purple metrics - only if files differ
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_purity', {}),
            self.analysis2.get('purple_purity', {}),
            "Purple Purity and Quality Metrics"
        ))

        # Driver counts
        lines.extend(self.compare_dict_metrics(
            {
                'somatic_drivers': len(self.analysis1.get('purple_drivers_somatic', [])),
                'germline_drivers': len(self.analysis1.get('purple_drivers_germline', []))
            },
            {
                'somatic_drivers': len(self.analysis2.get('purple_drivers_somatic', [])),
                'germline_drivers': len(self.analysis2.get('purple_drivers_germline', []))
            },
            "Purple Driver Counts"
        ))

        # PAVE-CRITICAL: Purple QC metrics comparison
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_qc', {}),
            self.analysis2.get('purple_qc', {}),
            "Purple QC Metrics"
        ))

        # PAVE-CRITICAL: CNV analysis
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_cnv_somatic', {}),
            self.analysis2.get('purple_cnv_somatic', {}),
            "Purple CNV Somatic Segments"
        ))

        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('purple_cnv_gene', {}),
            self.analysis2.get('purple_cnv_gene', {}),
            "Purple CNV Gene Level"
        ))

        # Key cancer genes CNV comparison
        lines.extend(self.compare_key_cancer_genes_cnv())

        # PAVE-CRITICAL: AF tables comparison (purity-dependent calculations)
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('af_tumor_keygenes', {}),
            self.analysis2.get('af_tumor_keygenes', {}),
            "Tumor AF Key Genes (purity-dependent)"
        ))

        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('af_tumor_global', {}),
            self.analysis2.get('af_tumor_global', {}),
            "Tumor AF Global (purity-dependent)"
        ))

        # PAVE-CRITICAL: TMB/MSI metrics
        lines.extend(self.compare_content_with_md5_check(
            self.analysis1.get('tmb_msi_metrics', {}),
            self.analysis2.get('tmb_msi_metrics', {}),
            "TMB/MSI Metrics (purity-adjusted)"
        ))

        # DRAGEN-specific MultiQC metrics
        lines.extend(self.compare_dragen_metrics())

        # Pipeline steps - check MD5 and compare counts only if different
        lines.extend(self.compare_sv_pipeline_steps())

        # Overlap analysis - core PAVE impact assessment
        lines.extend(self.generate_overlap_analysis())

        # Deep-dive PCGR PASS differences - MNV splitting detection
        lines.extend(self.investigate_pcgr_differences())

        # Exemplar variants with explanations
        lines.extend(self.exemplar_unique_variants())

        return lines

def main():
    parser = argparse.ArgumentParser(
        description='Comprehensive SASH run comparison',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single pair comparison
  %(prog)s --run1 /path/to/run1/L2100780_L2100779 \\
           --run2 /path/to/run2/L2100780_L2100779 \\
           --tumor L2100780 --normal L2100779 \\
           --output comparison_output

  # Batch comparison with config file
  %(prog)s --config runs-config.yaml --output batch_comparison
        """
    )

    # Config file mode
    parser.add_argument('--config', help='YAML config file for batch processing')

    # Single pair mode
    parser.add_argument('--run1', help='First SASH run directory')
    parser.add_argument('--run2', help='Second SASH run directory')
    parser.add_argument('--alias1', default='Run1', help='Alias for first run (default: Run1)')
    parser.add_argument('--alias2', default='Run2', help='Alias for second run (default: Run2)')
    parser.add_argument('--tumor', help='Tumor sample ID')
    parser.add_argument('--normal', help='Normal sample ID')

    # Output options
    parser.add_argument('--output', '--out', required=True, help='Output directory')

    args = parser.parse_args()

    setup_run_logging(args.output, args.config)

    # Validate arguments
    if args.config:
        # Batch mode
        return run_batch_mode(args)
    else:
        # Single pair mode - require all parameters
        if not all([args.run1, args.run2, args.tumor, args.normal]):
            parser.error("Single pair mode requires --run1, --run2, --tumor, and --normal")
        return run_single_pair(args)


def _analyze_run(run_path: str, tumor_id: str, normal_id: str, alias: str) -> tuple:
    """Analyze a SASH run and return (analyzer, analysis) tuple."""
    print(f"  Analyzing {alias}...")
    analyzer = SashRunAnalyzer(run_path, tumor_id, normal_id)
    analysis = analyzer.analyze_run()
    analysis['enhanced_vcf_analysis'] = analyzer.enhanced_vcf_analysis()
    analysis['directory_analysis'] = analyzer.explore_subdirectories()
    return analyzer, analysis


def _safe_float(value: Any) -> float | None:
    """Convert numeric-like value to float when possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_numeric_by_token(payload: Any, token: str) -> float | None:
    """Recursively find first numeric value where a key contains the token."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if token in str(key).lower():
                numeric = _safe_float(value)
                if numeric is not None:
                    return numeric
            found = _extract_numeric_by_token(value, token)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_numeric_by_token(item, token)
            if found is not None:
                return found
    return None


def _build_compact_summary(
    pair_name: str,
    tumor_id: str,
    normal_id: str,
    metadata: dict,
    comparison_metrics: dict
) -> dict:
    """Build compact summary artifact intended for Lambda final status logging.

    No WARN band: any numeric metric delta beyond floating-point noise (>= NUMERIC_DELTA_EPSILON)
    is critical. A bug-fix claim needs to hold up under manual NATA review, not a tolerance band —
    see docs/comparison-thresholds.md.
    """
    file_comparison = comparison_metrics.get('file_comparison', {})
    critical_items = []

    missing_total = (
        int(file_comparison.get('missing_run1', 0))
        + int(file_comparison.get('missing_run2', 0))
        + int(file_comparison.get('missing_both', 0))
    )
    if missing_total > 0:
        critical_items.append(f"missing_key_files:{missing_total}")

    # Changed clinical output files are critical — requires human sign-off regardless of update type
    different_keys = file_comparison.get('different_keys', [])
    if different_keys:
        critical_items.append(f"changed_key_files:{','.join(different_keys)}")
    elif int(file_comparison.get('different', 0)) > 0:
        # Backward compat: old metrics.json without different_keys
        critical_items.append(f"changed_key_files:{file_comparison['different']}")

    run1_purple = (comparison_metrics.get('purple', {}).get('run1') or {})
    run2_purple = (comparison_metrics.get('purple', {}).get('run2') or {})
    for metric_name in ('purity', 'ploidy'):
        v1 = _safe_float(run1_purple.get(metric_name))
        v2 = _safe_float(run2_purple.get(metric_name))
        if v1 is None or v2 is None:
            continue
        delta = abs(v2 - v1)
        if delta >= NUMERIC_DELTA_EPSILON:
            critical_items.append(f"{metric_name}_delta:{delta:.6f}")

    run1_multiqc = comparison_metrics.get('multiqc', {}).get('run1')
    run2_multiqc = comparison_metrics.get('multiqc', {}).get('run2')
    for token in ('tmb', 'msi'):
        m1 = _extract_numeric_by_token(run1_multiqc, token)
        m2 = _extract_numeric_by_token(run2_multiqc, token)
        if m1 is None or m2 is None:
            continue
        delta = abs(m2 - m1)
        if delta >= NUMERIC_DELTA_EPSILON:
            critical_items.append(f"{token}_delta:{delta:.6f}")

    status = 'FAIL' if critical_items else 'PASS'

    return {
        'pair': pair_name,
        'tumor_id': tumor_id,
        'normal_id': normal_id,
        'metadata': metadata or {},
        'status': status,
        'file_comparison': {
            'identical': int(file_comparison.get('identical', 0)),
            'different': int(file_comparison.get('different', 0)),
            'missing': missing_total,
        },
        'critical_count': len(critical_items),
        'critical_items': critical_items,
        'metrics_impacted': bool(critical_items),
    }


def _write_comparison_output(
    pair_output: Path,
    pair_name: str,
    tumor_id: str,
    normal_id: str,
    analyzer1,
    analysis1: dict,
    alias1: str,
    analyzer2,
    analysis2: dict,
    alias2: str,
    metadata: dict | None = None
):
    """Generate comparison and write report + JSON output."""
    print("  Generating comparison...")
    reporter = ComparisonReporter(analysis1, analysis2, alias1, alias2)

    # Write text report
    report_file = pair_output / 'report.txt'
    with open(report_file, 'w') as f:
        f.write('\n'.join(reporter.generate_comprehensive_report()))
    print(f"  Report: {report_file}")

    # Build and write JSON metrics
    comparison_metrics = reporter.extract_comparison_metrics()
    metrics = {
        'pair': pair_name,
        'tumor_id': tumor_id,
        'normal_id': normal_id,
        'metadata': metadata or {},
        'run1': {
            'path': str(analyzer1.base_dir),
            'alias': alias1,
            'base_dir': str(analyzer1.base_dir),
            'analysis': analysis1
        },
        'run2': {
            'path': str(analyzer2.base_dir),
            'alias': alias2,
            'base_dir': str(analyzer2.base_dir),
            'analysis': analysis2
        },
        'comparison': comparison_metrics
    }

    json_file = pair_output / 'metrics.json'
    with open(json_file, 'w') as f:
        json.dump(clean_for_json(metrics), f, indent=2)
    print(f"  JSON metrics: {json_file}")

    summary = _build_compact_summary(pair_name, tumor_id, normal_id, metadata or {}, comparison_metrics)
    summary_file = pair_output / 'summary.json'
    with open(summary_file, 'w') as f:
        json.dump(clean_for_json(summary), f, indent=2)
    print(f"  Compact summary: {summary_file}")


def run_single_pair(args):
    """Run comparison for a single tumor-normal pair."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    pair_name = f"{args.tumor}_{args.normal}"

    print(f"\n{'='*80}")
    print(f"Comparing pair: {pair_name}")
    print(f"  Run 1: {args.run1} ({args.alias1})")
    print(f"  Run 2: {args.run2} ({args.alias2})")
    print(f"{'='*80}\n")

    analyzer1, analysis1 = _analyze_run(args.run1, args.tumor, args.normal, args.alias1)
    analyzer2, analysis2 = _analyze_run(args.run2, args.tumor, args.normal, args.alias2)

    _write_comparison_output(
        output_dir, pair_name, args.tumor, args.normal,
        analyzer1, analysis1, args.alias1,
        analyzer2, analysis2, args.alias2
    )

    return 0


def run_batch_mode(args):
    """Run comparison for multiple pairs using config file."""
    # Load config
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    pairs_dir = output_dir / 'pairs'
    pairs_dir.mkdir(exist_ok=True)

    # Check config format - support two styles
    if 'baseline' in config and 'runs' in config:
        # New format with baseline and runs
        return run_batch_mode_new_format(args, config, output_dir, pairs_dir)
    elif 'pairs' in config:
        # Simple format with run1/run2 in each pair
        return run_batch_mode_simple_format(args, config, output_dir, pairs_dir)
    else:
        raise ValueError("Config must have either 'baseline'+'runs' or 'pairs' with run1/run2")


def run_batch_mode_simple_format(args, config, output_dir, pairs_dir):
    """Process config with run1/run2 specified per pair."""
    pairs = config['pairs']

    print(f"\n{'='*80}")
    print("BATCH COMPARISON MODE (Simple Format)")
    print(f"{'='*80}")
    print(f"Pairs to process: {len(pairs)}")
    print(f"{'='*80}\n")

    completed = 0
    for pair_config in pairs:
        tumor_id = pair_config['tumor']
        normal_id = pair_config['normal']
        pair_name = f"{tumor_id}_{normal_id}"
        run1_path = Path(pair_config['run1'])
        run2_path = Path(pair_config['run2'])

        # Extract metadata if present
        pair_metadata = pair_config.get('metadata', {})

        completed += 1

        print(f"\n[{completed}/{len(pairs)}] Processing: {pair_name}")
        print(f"  Run1: {run1_path}")
        print(f"  Run2: {run2_path}")

        # Check if directories exist
        if not run1_path.exists():
            print(f"  Warning: {run1_path} not found, skipping")
            continue
        if not run2_path.exists():
            print(f"  Warning: {run2_path} not found, skipping")
            continue

        # Create pair output directory
        pair_output = pairs_dir / pair_name
        pair_output.mkdir(exist_ok=True)

        # Check for global aliases first, then pair-specific, then defaults
        alias1 = pair_config.get('alias1') or config.get('alias_run1', 'Run1')
        alias2 = pair_config.get('alias2') or config.get('alias_run2', 'Run2')

        # Try to extract version from path only if no alias specified at all
        if 'alias1' not in pair_config and 'alias_run1' not in config:
            if '070' in str(run1_path) or '0.7.0' in str(run1_path):
                alias1 = 'SASH 0.7.0'
            elif '06' in str(run1_path) or 'older' in str(run1_path):
                alias1 = 'SASH 0.6.X'

        if 'alias2' not in pair_config and 'alias_run2' not in config:
            if '070' in str(run2_path) or '0.7.0' in str(run2_path):
                alias2 = 'SASH 0.7.0'
            elif '06' in str(run2_path) or 'older' in str(run2_path):
                alias2 = 'SASH 0.6.X'

        try:
            analyzer1, analysis1 = _analyze_run(str(run1_path), tumor_id, normal_id, alias1)
            analyzer2, analysis2 = _analyze_run(str(run2_path), tumor_id, normal_id, alias2)

            _write_comparison_output(
                pair_output, pair_name, tumor_id, normal_id,
                analyzer1, analysis1, alias1,
                analyzer2, analysis2, alias2,
                pair_metadata
            )

        except Exception as e:
            print(f"  Error processing {pair_name}: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n{'='*80}")
    print("Batch processing complete!")
    print(f"Output directory: {output_dir}")
    print(f"Pair metrics: {pairs_dir}")
    print(f"{'='*80}\n")

    return 0


def run_batch_mode_new_format(args, config, output_dir, pairs_dir):
    """Process config with baseline and runs list."""
    # Get baseline run
    baseline_id = config.get('baseline')
    if not baseline_id:
        raise ValueError("Config must specify 'baseline' run ID")

    # Find baseline run
    baseline_run = None
    for run in config['runs']:
        if run['id'] == baseline_id:
            baseline_run = run
            break

    if not baseline_run:
        raise ValueError(f"Baseline run '{baseline_id}' not found in config")

    # Get other runs to compare against baseline
    comparison_runs = [r for r in config['runs'] if r['id'] != baseline_id]

    if not comparison_runs:
        raise ValueError("No comparison runs found (need at least 2 runs total)")

    # Process each pair
    total_pairs = len(config['pairs']) * len(comparison_runs)
    completed = 0

    # Helper to pick a sensible alias for display: prefer first alias, then label, then id
    def _pick_alias(run_dict: dict) -> str:
        aliases = run_dict.get('aliases')
        if isinstance(aliases, list) and aliases:
            return aliases[0]
        return run_dict.get('label') or run_dict.get('id')

    print(f"\n{'='*80}")
    print("BATCH COMPARISON MODE")
    print(f"{'='*80}")
    print(f"Baseline: {_pick_alias(baseline_run)} ({baseline_run.get('path')})")
    print(f"Comparing against: {', '.join(_pick_alias(r) for r in comparison_runs)}")
    print(f"Pairs to process: {len(config['pairs'])}")
    print(f"Total comparisons: {total_pairs}")
    print(f"{'='*80}\n")

    for pair_config in config['pairs']:
        tumor_id = pair_config['tumor']
        normal_id = pair_config['normal']
        pair_name = f"{tumor_id}_{normal_id}"
        pair_metadata = pair_config.get('metadata', {})

        for comp_run in comparison_runs:
            completed += 1

            print(f"\n[{completed}/{total_pairs}] Processing: {pair_name}")
            print(f"  {baseline_run['label']} vs {comp_run['label']}")

            pair_output = pairs_dir / pair_name
            pair_output.mkdir(exist_ok=True)

            run1_path = Path(baseline_run['path']) / pair_name
            run2_path = Path(comp_run['path']) / pair_name

            if not run1_path.exists():
                print(f"  Warning: {run1_path} not found, skipping")
                continue
            if not run2_path.exists():
                print(f"  Warning: {run2_path} not found, skipping")
                continue

            alias1 = _pick_alias(baseline_run)
            alias2 = _pick_alias(comp_run)

            try:
                analyzer1, analysis1 = _analyze_run(str(run1_path), tumor_id, normal_id, alias1)
                analyzer2, analysis2 = _analyze_run(str(run2_path), tumor_id, normal_id, alias2)

                _write_comparison_output(
                    pair_output, pair_name, tumor_id, normal_id,
                    analyzer1, analysis1, alias1,
                    analyzer2, analysis2, alias2,
                    pair_metadata
                )

            except Exception as e:
                print(f"  Error processing {pair_name}: {e}")
                continue

    print(f"\n{'='*80}")
    print("Batch processing complete!")
    print(f"Output directory: {output_dir}")
    print(f"Pair metrics: {pairs_dir}")
    print(f"{'='*80}\n")

    return 0


if __name__ == '__main__':
    main()
