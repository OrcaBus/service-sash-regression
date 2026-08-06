# Comparison thresholds

How the Comparator decides `PASS` / `FAIL`, and the open accreditation question that motivated
writing this down.

## No WARN band

There used to be a `WARN` status for numeric deltas below `0.05`. It's gone: we couldn't define
what a "relevant" warning would mean for an accreditation bug-fix claim, so any real difference
now fails the pair and gets a human look, same as a changed clinical file. `PASS` means no
detected difference at all, not "a difference small enough to ignore."

The removal is structural, not cosmetic — `warning_count` and `warning_items` are gone from the
summary artifact, and a run can only be `PASS`, `FAIL`, or `MANUAL_CHECK`.

## File-level: any difference fails

Eight "key files" — `purple_purity`, `purple_qc`, `purple_somatic_vcf`, `purple_sv_vcf`,
`pcgr_tiers`, `cpsr_tiers`, `somatic_bcftools`, `germline_bcftools` — are compared by md5.
Any byte difference adds the key to `different_keys`, which is always critical. No threshold
applies here — a single changed tier count or a new driver gene call is enough to fail the pair.

## Numeric metrics: fail on anything above floating-point noise

Four metrics — purity, ploidy, TMB, MSI — are compared against `NUMERIC_DELTA_EPSILON = 1e-6`:

```python
delta = abs(v2 - v1)
if delta >= NUMERIC_DELTA_EPSILON:
    critical_items.append(...)   # -> FAIL
```

- `1e-6` is the same epsilon the report generator already used elsewhere to treat two floats as
  "the same value" — it exists to absorb serialization/rounding noise, not to tolerate real
  biological or reporting variation.
- A pair only reaches `PASS` if no key file differs and no numeric delta clears that noise floor.

## Where this lives in the code

Deliberately named by symbol rather than line number — this file went stale once already when the
line numbers drifted.

| What                                                             | Where                                                                           |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `NUMERIC_DELTA_EPSILON` and the pair-level PASS/FAIL decision    | `_build_compact_summary` in `app/comparator/comprehensive_sash_comparison.py`   |
| Run-level rollup across pairs (`FAIL` > `MANUAL_CHECK` > `PASS`) | `_build_compact_summary` in `app/comparator/lambdas/comparator/handler.py`      |
| Per-pair status derived from `summary.json`                      | `_derive_pair_compact_status` in `app/comparator/lambdas/comparator/handler.py` |
| Tests                                                            | `app/tests/test_compact_summary.py`, `app/tests/test_comparator_handler.py`     |
