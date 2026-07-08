# Codebase Concerns

**Analysis Date:** 2026-07-01

## Tech Debt

**`comprehensive_sash_comparison.py` — monolithic 3,680-line God file:**

- Issue: All analysis logic, comparison logic, reporter, batch modes, and CLI `main()` live in a single script. The `SashRunAnalyzer` class alone spans ~1,800 lines.
- Files: `app/comparator/comprehensive_sash_comparison.py`
- Impact: High cognitive load to navigate. Difficult to test individual analysis methods in isolation. Any change risks unintended side effects on unrelated sections.
- Fix approach: Extract `SashRunAnalyzer`, `ComparisonReporter`, batch-mode functions, and CLI entry point into separate modules under `app/comparator/`.

**No unit tests for `comprehensive_sash_comparison.py`:**

- Issue: The most complex file in the codebase — with ~40 parsing methods and multiple comparison algorithms — has zero direct test coverage. `test_comparison.py` only tests the `subprocess.run` wrapper in `comparison.py`, not the analysis logic itself.
- Files: `app/comparator/comprehensive_sash_comparison.py`, `app/tests/test_comparison.py`
- Impact: Parser regressions (e.g., bcftools stats line format changes, tier column name changes, PCGR filename pattern changes) go undetected until a Lambda invocation fails in production.
- Fix approach: Extract parsers into testable functions and add unit tests with fixture VCF/TSV snippets.

**VCF variant analysis 10,000-record cap in `vcf_analysis()`:**

- Issue: `vcf_analysis()` breaks after the 10,000th variant (`if variant_count > 10000: break`) without warning. This is separate from `count_vcf_variants()` which reads the full file using cyvcf2/pysam.
- Files: `app/comparator/comprehensive_sash_comparison.py` (line ~756)
- Impact: PCGR PASS VCFs from high-TMB samples may exceed 10,000 records. The truncated analysis produces incorrect per-file statistics while the method returns silently, producing incorrect `header_info`, annotation counts, and variant examples.
- Fix approach: Remove the cap or make it configurable; add a logged warning if the cap is hit.

**`numpy` imported lazily inside a nested function:**

- Issue: `import numpy as np` appears inside `summarize_unique()` at line 2391, which is inside `investigate_pcgr_differences()`. NumPy is not in the class-level imports and this pattern is fragile — if numpy is not installed the error surfaces deep in a nested call with an opaque traceback.
- Files: `app/comparator/comprehensive_sash_comparison.py` (line 2391)
- Impact: If numpy is ever removed from `requirements.txt` the error surfaces at runtime deep in the report generation path, not at import time.
- Fix approach: Move `import numpy as np` to the module top level alongside other imports.

**`glob` imported inside a method:**

- Issue: `import glob` appears inside `parse_cancer_report_table()` at line ~1447, not at module top.
- Files: `app/comparator/comprehensive_sash_comparison.py` (line ~1447)
- Impact: Minor — lazy imports are a code smell that makes dependency analysis harder and increases per-call overhead (negligible but not idiomatic).
- Fix approach: Move to module-level imports.

**`from collections import Counter` inside nested function:**

- Issue: `from collections import Counter` is re-imported inside `summarize_unique()` at line ~2364, which is a nested function inside `investigate_pcgr_differences()`. Counter is already used at module level via `from collections import Counter` at the top.
- Files: `app/comparator/comprehensive_sash_comparison.py` (line ~2364)
- Impact: Dead code / misleading — the module-level import already covers this. Suggests the nested function was extracted from another file without cleanup.
- Fix approach: Remove the redundant import inside the nested function.

**Lambda shared IAM role for both functions:**

- Issue: `createComparatorFunction` and `createSubmitterFunction` share the same `lambdaRole`. The comparator does not need `secretsmanager:GetSecretValue`, `ssm:GetParameter`, `lambda:InvokeFunction`, or `events:PutEvents`. The submitter does not need broad `s3:GetObject`/`s3:ListBucket` on `pipeline-*-cache-*` or `project-data-*`.
- Files: `infrastructure/stage/deployment-stack.ts`
- Impact: Violates principle of least privilege. If either Lambda is compromised, the attacker has all permissions of both.
- Fix approach: Create separate `comparatorRole` and `submitterRole` with only the permissions each function needs.

**Hardcoded `wruDraftValidatorFunctionName` in constants:**

- Issue: `wruDraftValidatorFunctionName` in `getStageConstants` returns the same hardcoded function name regardless of the `_stage` parameter. The `_stage` argument is explicitly suppressed with an ESLint disable comment `// eslint-disable-next-line @typescript-eslint/no-unused-vars`.
- Files: `infrastructure/stage/constants.ts` (line 26)
- Impact: Cannot deploy to beta/gamma with a stage-appropriate WRU validator. All stages hit the same Lambda, which is likely the production one.
- Fix approach: Add a per-stage map for `wruDraftValidatorFunctionName` like other constants.

**In-memory token and hostname caching in `submit.py`:**

- Issue: `_token_cache` and `_hostname_cache` are module-level globals. Lambda execution environments can be reused across invocations for up to 15 minutes (Lambda function timeout). An OrcaBus token has a finite TTL (typically shorter than 15 min). When the token expires, subsequent invocations with a cached stale token will get 401s until the Lambda environment is recycled.
- Files: `app/submitter/submit.py` (lines 37–38)
- Impact: Token expiry produces opaque `raise_for_status()` HTTP 401 errors with no retry or refresh logic.
- Fix approach: Cache with a TTL (e.g., store `(token, fetched_at)` and re-fetch if older than 10 minutes).

**`_find_existing_run` loads all runs client-side:**

- Issue: The function fetches all workflow runs for a given `codeVersion` and iterates them in Python to find matching libraries. If many regression runs exist for a given version, this can return a large paginated result set but the code only reads the first page (`data.get("results", [])`).
- Files: `app/submitter/submit.py` (lines 102–122)
- Impact: Silently misses existing runs when pagination is needed. Could lead to duplicate run submissions for the same version/libraries pair.
- Fix approach: Handle pagination by iterating `next` links from the API response, or add library-level query filters to narrow the server-side result.

**`s3_utils.py` creates one `boto3.client("s3")` per call:**

- Issue: `download_s3_dir()` and `upload_file()` each call `boto3.client("s3")` inside the function body. In the comparator handler these are called once per file upload (potentially hundreds of files per pair).
- Files: `app/comparator/s3_utils.py`
- Impact: Each `boto3.client()` call constructs a new client object and performs credential resolution. With large output directories (many `.vcf.gz`, `.tsv`, `.json.gz` files), this creates noticeable overhead.
- Fix approach: Pass a shared `boto3.client("s3")` instance into these functions, or use a module-level cached client.

**`cdk.out/` committed to the repository:**

- Issue: Multiple `cdk.out/asset.*` directories are committed containing full copies of the Python application source (including tests) for multiple synthesis snapshots.
- Files: `cdk.out/asset.050ae2f5.../`, `cdk.out/asset.97a078ce.../`, `cdk.out/asset.c0a6c6e4.../`, `cdk.out/asset.e9f2de31.../`, `cdk.out/asset.f3286622.../`
- Impact: Repository size bloat (5+ full copies of the app). Changes to source files require re-synth to keep `cdk.out/` current; stale snapshots are misleading. The `cdk.out/asset.c0a6c6e4.../` copy is missing the submitter module entirely, suggesting it predates the submitter feature.
- Fix approach: Add `cdk.out/` to `.gitignore`. CDK snapshot tests should run from a synth in CI, not from committed artifacts.

## Known Bugs

**`_derive_pair_compact_status()` — legacy `metrics.json` fallback overcounts file changes:**

- Symptoms: When `comprehensive_sash_comparison.py` produces `metrics.json` (legacy path — no `summary.json`), the fallback reads `comparison.comparison.file_comparison` from the nested object. However `extract_comparison_metrics()` stores `file_comparison` at the top level of `metrics.json`, not nested under `comparison.comparison`. The double-nesting `(comparison.get("comparison") or {}).get("file_comparison")` evaluates to `{}` for any current `metrics.json` output, causing all PASS results to be emitted even when files differ.
- Files: `app/comparator/lambdas/comparator/handler.py` (lines 78–103)
- Trigger: Only reachable when `comparison.py` reads `metrics.json` (no `summary.json` produced). This was the code path before `summary.json` was added.
- Workaround: The `summary.json`-preferred path (lines 67–76) is correct and takes precedence when the script produces `summary.json`, which it now always does.

**`run_batch_mode_new_format()` computes `total_pairs` incorrectly:**

- Symptoms: `total_pairs = len(config['pairs']) * len(comparison_runs)` is computed, but the loop iterates `config['pairs']` then `comparison_runs` — resulting in the correct pair count being printed. However `config['pairs']` in "new format" contains tumor/normal metadata, NOT the run directories. Run paths are constructed from `baseline_run['path'] / pair_name`. If `pair_name` uses `_` separator but actual dirs use `__` separator, the directories won't exist and will be silently skipped.
- Files: `app/comparator/comprehensive_sash_comparison.py` (lines 3584–3676)
- Trigger: Running batch mode with a config in the "new format" (`baseline` + `runs` keys) when pair directory names use double-underscore separators.

**`comparison.py` swallows non-zero exit codes when any output exists:**

- Symptoms: `if result.returncode != 0 and not summary_path.exists() and not metrics_path.exists()` — if the comparison script fails partway through but has written a partial `summary.json`, the error is logged as a warning and the partial output is used as if it were complete.
- Files: `app/comparator/comparison.py` (lines 32–36)
- Trigger: Any crash in `comprehensive_sash_comparison.py` after `summary.json` is written but before all analyses are complete.
- Workaround: Partial `summary.json` may still be complete if the crash occurs in a later (non-critical) section.

## Security Considerations

**S3 path traversal protection present but incomplete:**

- Risk: `download_s3_dir()` has a path-traversal check (`if not str(dest).startswith(str(local_dir.resolve()))`), but the check uses string prefix matching which can be fooled by paths like `/tmp/safe_dir_X` matching `/tmp/safe_dir` if the suffix is chosen carefully.
- Files: `app/comparator/s3_utils.py` (line 25)
- Current mitigation: The check catches the most obvious cases. Lambda ephemeral storage (`/tmp`) is isolated per invocation so traversal outside the temp dir is the primary concern.
- Recommendations: Replace the string prefix check with `Path.is_relative_to()` (Python 3.9+) which performs proper path comparison without string matching vulnerabilities.

**Broad wildcard S3 permissions on pipeline cache buckets:**

- Risk: The Lambda role grants `s3:GetObject` and `s3:ListBucket` on `arn:aws:s3:::pipeline-*-cache-*` and `arn:aws:s3:::project-data-*`. These are broad wildcard patterns that include all pipeline cache buckets and all project data buckets in the account, not only the sash-regression relevant ones.
- Files: `infrastructure/stage/deployment-stack.ts` (lines 43–53)
- Current mitigation: Read-only permissions only — no `s3:PutObject` or `s3:DeleteObject`.
- Recommendations: Restrict to specific bucket names when they are known; at minimum document why the wildcard is necessary.

**OrcaBus token stored in module-level global:**

- Risk: The JWT token retrieved from Secrets Manager is cached as a module-level string `_token_cache`. If the Lambda environment is shared (execution environment reuse), this token persists in memory for the lifetime of the environment.
- Files: `app/submitter/submit.py` (lines 37, 41–47)
- Current mitigation: AWS Lambda execution environments are single-tenant and not shared across accounts or customers.
- Recommendations: Add TTL-based cache expiry to reduce exposure window; document the expected token lifetime.

**`PAYLOAD_VERSION` hardcoded string:**

- Risk: `PAYLOAD_VERSION = "2025.08.05"` is a hardcoded date string from August 2025 embedded in every WRU draft payload. If the OrcaBus payload schema is versioned, submitting an outdated version may cause silent rejection or unexpected behaviour.
- Files: `app/submitter/submit.py` (line 29)
- Current mitigation: None detected.
- Recommendations: Surface as a configurable constant (environment variable or CDK parameter) so it can be updated without code changes.

## Performance Bottlenecks

**Full sash output download into Lambda ephemeral storage:**

- Problem: `_run_pair()` calls `download_s3_dir()` for both baseline and new run directories into `/tmp`. For a single pair, sash outputs can be multiple gigabytes (large VCFs, BAM summaries, MultiQC data). The Lambda ephemeral storage is set to 10 GiB, but download time adds directly to execution time.
- Files: `app/comparator/lambdas/comparator/handler.py` (lines 134–135), `infrastructure/stage/deployment-stack.ts` (line 125)
- Cause: The comparator downloads all files even when most are not needed for comparison (e.g., HTML reports, BAM index files).
- Improvement path: Implement selective download — only fetch the files referenced in `EXPECTED_FILES` and the key analysis paths used by `comprehensive_sash_comparison.py`. Alternatively, use S3 Select or signed URLs for direct file reads.

**MD5 hashing every file in `analyze_run()`:**

- Problem: `_calculate_file_md5()` is called for every tracked file in `analyze_run()`. For large files (e.g., `purple.somatic.vcf.gz`, bcftools stats), this reads the entire file into memory in 4 KB chunks to compute a hash that is only used to detect changes.
- Files: `app/comparator/comprehensive_sash_comparison.py` (lines 246–259, 1612–1864)
- Cause: MD5 is computed before any content-based comparison, so all files are hashed even when only a subset differ.
- Improvement path: Use S3 ETags (already available from `list_objects_v2` responses) as a proxy for MD5 on files not requiring content parsing; reserve local MD5 computation for files that have already been identified as changed.

**`comprehensive_sash_comparison.py` run as a subprocess:**

- Problem: `comparison.py` invokes `comprehensive_sash_comparison.py` via `subprocess.run()`, which adds process-spawn overhead (Python startup, import time for pandas/boto3/etc.) and passes data through filesystem files (`summary.json`, `metrics.json`) rather than in-process return values.
- Files: `app/comparator/comparison.py` (lines 18–27)
- Cause: The script was originally a standalone CLI tool used before the Lambda integration existed. The subprocess boundary was retained when integrating.
- Improvement path: Refactor `comprehensive_sash_comparison.py` to expose a callable Python API (`run_pair_comparison(run1, run2, tumor, normal, output_dir) -> dict`) and call it directly from `comparison.py`.

## Fragile Areas

**`_get_base_dir()` — version-specific directory layout detection:**

- Files: `app/comparator/comprehensive_sash_comparison.py` (lines 120–154)
- Why fragile: The method contains hard-coded logic for sash 0.6.x vs 0.7.0 directory layout differences (`{tumor}_{normal}` vs `{tumor}__{normal}`). If a future sash version introduces another layout change, this silently falls back to the double-underscore pattern, which may not exist.
- Safe modification: Add a logged warning when falling back to the constructed path that doesn't exist; add a test with a tmp_path fixture for each known layout.
- Test coverage: No direct tests for this method.

**`schema_check.py` — hardcoded `EXPECTED_FILES` list:**

- Files: `app/comparator/schema_check.py`
- Why fragile: The 9 expected output files are a hardcoded list at module level. If sash renames or moves an output file between versions, schema checks start failing for ALL versions, not just the new one. There is no per-version schema definition.
- Safe modification: Load expected files from the testdata config YAML (where per-case metadata already lives) or accept an optional override list.
- Test coverage: Covered by `app/tests/test_schema_check.py`.

**`constants.ts` — single `getStageConstants()` ignores stage:**

- Files: `infrastructure/stage/constants.ts` (lines 26–33)
- Why fragile: The function signature accepts `_stage: StageName` but ignores it. All three pipeline stages (beta, gamma, prod) deploy with identical constants. If prod-specific values are ever needed, this silently uses the wrong ones.
- Safe modification: Expand to a stage-keyed map before adding any per-stage logic.
- Test coverage: CDK snapshot tests in `test/stage.test.ts` exercise this but only check the synthesized stack, not that stage differences are correctly applied.

**`parse_cancer_report_table()` — glob fallback picks arbitrary first match:**

- Files: `app/comparator/comprehensive_sash_comparison.py` (lines 1453–1458)
- Why fragile: When the primary filename patterns don't match, a glob pattern `*-{suffix}` picks `matches[0]` without sorting. File order from `glob.glob` is OS-dependent (undefined on Linux, alphabetical on macOS). Different filesystems can pick different files.
- Safe modification: Sort `matches` before selecting `matches[0]`.
- Test coverage: None.

**`_create_portal_run_id()` — non-cryptographic randomness:**

- Files: `app/submitter/submit.py` (lines 62–64)
- Why fragile: The 8-character hex suffix uses `random.choices()` (Python's Mersenne Twister), not `secrets.token_hex()`. For a unique ID, this is adequate, but if the ID is ever used in a security context (e.g., as a nonce or idempotency token), the predictable PRNG is a weakness.
- Safe modification: Replace with `secrets.token_hex(4)` (4 bytes = 8 hex chars, cryptographically random).
- Test coverage: `test_portal_run_id_format` in `test_submit.py` checks format only, not entropy.

## Scaling Limits

**Lambda timeout at 15 minutes for multi-pair invocations:**

- Current capacity: The comparator Lambda has a 15-minute timeout (`Duration.minutes(15)`).
- Limit: Each pair requires: (a) full S3 download of both run dirs, (b) MD5 hashing all files, (c) subprocess invocation of the 3,680-line comparison script with pandas + VCF parsing. With multiple pairs in a single invocation, 15 minutes can be exhausted.
- Scaling path: Process one pair per Lambda invocation (fan-out pattern) or implement a Step Functions state machine to parallelize pairs. The current sequential loop in `handler()` (`for pair in pairs`) is the bottleneck.

**10 GiB ephemeral storage shared across all pairs in one invocation:**

- Current capacity: `Size.gibibytes(10)` of Lambda `/tmp`.
- Limit: With multiple pairs, each pair's downloaded files accumulate in `/tmp` inside the `with tempfile.TemporaryDirectory()` context. The context manager handles cleanup after all pairs complete, not after each pair. Large sash runs can reach 5–10 GB per pair.
- Scaling path: Move cleanup inside the pair loop: create and destroy a temp dir per pair, or process pairs sequentially with explicit `shutil.rmtree()` after each.

## Dependencies at Risk

**`run_logging.py` — `sys.stdout`/`sys.stderr` replacement with `Tee`:**

- Risk: `setup_run_logging()` replaces `sys.stdout` and `sys.stderr` with `Tee` instances at the process level. In a Lambda execution environment, CloudWatch Logs captures stdout/stderr at the Lambda runtime level. Replacing these with a `Tee` that writes to both the original stream and a log file adds an unexpected file write on the Lambda ephemeral filesystem for every `print()` call in the comparison script.
- Impact: The `Tee` is registered via `atexit` to restore streams, but Lambda freezes the execution environment between invocations — `atexit` handlers are NOT called between warm invocations. This means `log_handle` from a previous invocation may remain open and be written to again in the next warm invocation (until the file is flushed/closed by the OS when the environment is finally recycled).
- Files: `app/comparator/run_logging.py`
- Migration plan: Remove `atexit`-based stream restoration. In Lambda context, let CloudWatch capture stdout/stderr natively; only write the log file explicitly if local execution requires it (detect via `AWS_LAMBDA_FUNCTION_NAME` env var absence).

## Missing Critical Features

**No watcher/poller Lambda:**

- Problem: The submitter emits a `SashRegressionRunSubmitted` EventBridge event and the comparator is invoked manually. There is no watcher Lambda that polls OrcaBus for the submitted workflow run to complete and then automatically triggers the comparator. The `workflowRunName` embeds the baseline version specifically for a watcher to extract it, but no watcher exists yet.
- Blocks: End-to-end automation of the regression workflow. Currently requires manual comparator invocation with the correct S3 paths after the run completes.

**No notification on comparison completion:**

- Problem: The comparator Lambda logs `FINAL_RESULT` to CloudWatch and uploads results to S3, but there is no downstream notification (Slack, email, EventBridge event) to alert the operator that a comparison has finished and a result is available.
- Blocks: Operators must poll S3 or CloudWatch to learn that a comparison is done.

**No per-stage `wruDraftValidatorFunctionName`:**

- Problem: Only one WRU validator function name is configured regardless of stage. Deploying to beta/gamma uses the same (likely production) function.
- Blocks: Safe regression testing in non-prod environments.

## Test Coverage Gaps

**`comprehensive_sash_comparison.py` — zero unit tests:**

- What's not tested: All parsing methods (`parse_bcftools_stats`, `parse_purple_purity`, `parse_pcgr_msigs`, `parse_cancer_report_table`, `count_vcf_variants`, `_get_base_dir`, etc.), all `ComparisonReporter` methods, `_build_compact_summary`, batch mode execution paths.
- Files: `app/comparator/comprehensive_sash_comparison.py`
- Risk: Silent regressions in parser logic when sash output file formats change between versions.
- Priority: High — this is the core domain logic of the service.

**`run_logging.py` — no tests:**

- What's not tested: `Tee.write()`, `Tee.isatty()`, `setup_run_logging()` stream replacement, `atexit` registration.
- Files: `app/comparator/run_logging.py`
- Risk: Low for the Tee itself; medium for the atexit/Lambda interaction described in the Dependencies at Risk section above.
- Priority: Low.

**`comparison.py` — non-zero exit with existing output not covered:**

- What's not tested: The case where `returncode != 0` but `summary_path.exists()` (partial output). The swallowed-error path at lines 35–36.
- Files: `app/comparator/comparison.py`, `app/tests/test_comparison.py`
- Risk: Silent partial-result consumption goes undetected in tests.
- Priority: Medium.

---

_Concerns audit: 2026-07-01_
