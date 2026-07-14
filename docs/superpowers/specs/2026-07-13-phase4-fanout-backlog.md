# Backlog: per-pair fan-out, Aggregator, comparison-engine refactor

Date parked: 2026-07-13
Status: not scheduled — revisit if the test suite grows beyond one pair, or if
`comprehensive_sash_comparison.py`'s lack of unit tests becomes a real blocker

## Why this exists

During Phase 3 (Publisher) scoping, a `.kiro/specs/sash-regression-completion/design.md` rewrite
had independently expanded into a full service-completion overhaul: `PairComparatorFunction` +
`AggregatorFunction` for per-pair fan-out, a hand-rolled S3 conditional-write state machine, and a
7-module split of the 3,680-line `comprehensive_sash_comparison.py`. This was reconciled out of
Phase 3 (see `docs/superpowers/specs/2026-07-08-phase3-scope-discrepancy.md` — Resolution) because
it was never scoped with Florian and today's test suite is a single pair (SEQC-II-medium), so
there's no driving requirement yet.

The design work itself isn't wrong, just premature. Full content preserved here (and in git
history: branch `docs/completion-notification-design`, commit `f128335`, original
`.kiro/specs/sash-regression-completion/design.md`) so it isn't lost if/when this becomes real
scope — e.g. if the testdata matrix grows past ~2-3 pairs and the Watcher's 5-minute timeout or
sequential-processing time becomes a problem.

## Fan-out architecture (if revisited)

- **`WatcherFunction`** (extended): loads `testdata-cases.yaml`, generates one UUID `jobId` per
  fan-out batch, invokes `PairComparatorFunction` async (`InvocationType="Event"`) once per pair
  — fire-and-forget, never sequential/blocking.
- **`PairComparatorFunction`** (renamed from `ComparatorFunction`): processes exactly one
  tumor/normal pair per invocation. On unhandled exception, emits a `SashRegressionPairCompleted`
  event with `status: FAIL` from inside a try/except before re-raising, so the Aggregator always
  receives exactly `totalPairs` events even on Lambda failures (this "try/except emit" pattern is
  simpler than a DLQ and self-healing for aggregation — worth keeping if fan-out is built).
- **`AggregatorFunction`** (new): triggered by `SashRegressionPairCompleted`. Accumulates results
  in an S3-backed JSON state blob at
  `s3://umccr-research-dev/sash-regression/<new>-vs-<baseline>/jobs/<jobId>/state.json`, using S3
  conditional writes (`if-none-match` on create, versioning + CAS on update, retry ×3 on HTTP 412)
  to handle near-simultaneous pair completions without needing DynamoDB. When
  `receivedPairs == totalPairs`, computes a status rollup (worst-case: `FAIL` > `MANUAL_CHECK` >
  `WARN` > `PASS`), uploads `rollup.json`, and emits `SashRegressionComparisonCompleted`.
  Idempotent on duplicate `pairIndex` (dedup by index, so EventBridge's at-least-once delivery
  can't double-count a pair).

**Known bug in the original write-up, fix if implementing**: the design's S3 conditional-update
step named the header `CopySourceIfMatch` — that's a `CopyObject` header, not valid on
`PutObject`. Use `IfMatch`/`IfNoneMatch` (S3 conditional writes, GA since Aug 2024) instead.

## Comparison-engine refactor (if revisited)

Split `app/comparator/comprehensive_sash_comparison.py` (3,680 lines, zero unit tests) into
`app/comparator/analysis/`:

- `runner.py` — `run_pair_comparison()`, orchestrates all steps, replaces the current subprocess
  call with direct in-process function calls (saves ~2-5s Python startup per invocation, removes
  filesystem IPC for `summary.json`/`metrics.json`, removes partial-output-on-nonzero-exit risk)
- `vcf_parser.py` — `vcf_analysis()`, `count_vcf_variants()`; remove the current 10k-record cap,
  stream all variants, log a WARNING instead of truncating above 10k
- `tsv_parser.py` — `parse_purple_purity()`, `parse_cnv_somatic()`, `parse_prioritised_sv()`
- `stats_parser.py` — `parse_bcftools_stats()`
- `pcgr_parser.py` — `parse_cancer_report_table()`, `parse_pcgr_msigs()`
- `reporter.py` — `ComparisonReporter`, `build_compact_summary()`
- `base_dir.py` — `get_base_dir()`, handling both sash 0.6.x (`tumor_normal`) and 0.7.0+
  (`tumor__normal`) directory-naming layouts

Keep `comparison.py` as a backward-compat shim delegating to `analysis.runner`, and keep
`comprehensive_sash_comparison.py` retained as an invocable CLI script (for direct-invoke SOP
workflows) even after the refactor.

## Performance note

At 1 pair, the existing sequential Comparator has no timeout risk. Fan-out only starts mattering
if the pair count grows toward the ~10-pair ceiling mentioned in earlier scoping notes — at that
point each pair gets its own 15-minute Lambda budget and 10 GiB `/tmp`, instead of one Lambda
processing all pairs serially within a single timeout window.
