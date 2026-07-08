<!-- refreshed: 2026-07-01 -->

# Architecture

**Analysis Date:** 2026-07-01

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                    Invocation / Entry Points                          │
│  Direct Lambda invoke (Comparator) │ API Gateway POST (Submitter)    │
└──────────────────┬─────────────────────────────────┬─────────────────┘
                   │                                 │
                   ▼                                 ▼
┌──────────────────────────────┐   ┌──────────────────────────────────┐
│    Comparator Lambda         │   │    Submitter Lambda               │
│  `app/comparator/lambdas/    │   │  `app/submitter/lambdas/          │
│   comparator/handler.py`     │   │   submitter/handler.py`           │
│  - Load config from S3       │   │  - Parse event body               │
│  - Per-pair: schema + compare│   │  - Delegate to submit.py          │
│  - Upload results to S3      │   │  - Return action + portal_run_id  │
└──────────┬───────────────────┘   └──────────────┬───────────────────┘
           │                                       │
           ▼                                       ▼
┌──────────────────────────┐   ┌───────────────────────────────────────┐
│  comparator/ library     │   │  submitter/ library                   │
│  `app/comparator/`       │   │  `app/submitter/submit.py`            │
│  - schema_check.py       │   │  - OrcaBus REST API (find/submit run) │
│  - comparison.py         │   │  - WruDraftValidator Lambda invoke     │
│  - s3_utils.py           │   │  - EventBridge emit                   │
│  - comprehensive_sash_   │   │  - SecretsManager + SSM lookups       │
│    comparison.py (script)│   └──────────────┬────────────────────────┘
└──────────┬───────────────┘                  │
           │                                  │
           ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         AWS / S3                                      │
│  testdata bucket (read-only config + baseline reference data)         │
│  umccr-research-dev bucket (config read/write, results write)         │
│  pipeline-*-cache-* buckets (read sash outputs)                       │
│  OrcaBus EventBus (events:PutEvents for SashRegressionRunSubmitted)   │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component                     | Responsibility                                                                                                                 | File                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| Comparator handler            | Lambda entry point — orchestrates config load, per-pair schema+compare, result upload, compact summary                         | `app/comparator/lambdas/comparator/handler.py`    |
| schema_check                  | Validates presence of 9 required sash output files for a run                                                                   | `app/comparator/schema_check.py`                  |
| comparison                    | Wraps `comprehensive_sash_comparison.py` subprocess call, parses `summary.json` or `metrics.json` output                       | `app/comparator/comparison.py`                    |
| comprehensive_sash_comparison | Standalone Python script performing deep file-level comparison (VCF counts, Purple metrics, BCFtools stats, SV analysis)       | `app/comparator/comprehensive_sash_comparison.py` |
| s3_utils                      | Download S3 prefix to local dir, upload a single file to S3                                                                    | `app/comparator/s3_utils.py`                      |
| run_logging                   | Tee stdout/stderr to a log file; copy config; write command.txt                                                                | `app/comparator/run_logging.py`                   |
| Submitter handler             | Lambda entry point — parses API Gateway or direct event body, delegates to submit_sash_run                                     | `app/submitter/lambdas/submitter/handler.py`      |
| submit                        | OrcaBus integration: lookup workflow, find existing run, build DRAFT payload, invoke WruDraftValidator, emit EventBridge event | `app/submitter/submit.py`                         |
| SashRegressionStack           | CDK stack defining both Lambda functions, shared IAM role, API Gateway for Submitter                                           | `infrastructure/stage/deployment-stack.ts`        |
| StatelessStack                | CDK pipeline stack (CodePipeline → beta/gamma/prod)                                                                            | `infrastructure/toolchain/stateless-stack.ts`     |

## Pattern Overview

**Overall:** Two-Lambda microservice pattern backed by Docker images, deployed via AWS CDK CodePipeline.

**Key Characteristics:**

- Both Lambdas share a single Docker image built from `app/` — the Comparator Lambda uses the default CMD; the Submitter overrides CMD at deploy time
- Comparator is a long-running batch job (up to 15 min, 4 GB RAM, 10 GB ephemeral storage) — invoked directly, not via API
- Submitter is a lightweight REST handler (5 min, 512 MB) fronted by API Gateway
- No persistent database — state is derived from S3 objects and OrcaBus REST API at invocation time
- Comparison core (`comprehensive_sash_comparison.py`) runs as a subprocess spawned by `comparison.py` to isolate its heavy dependencies

## Layers

**Lambda handlers (entry points):**

- Purpose: Parse event, validate inputs, orchestrate calls, return structured response
- Location: `app/comparator/lambdas/comparator/handler.py`, `app/submitter/lambdas/submitter/handler.py`
- Contains: `handler(event, context)` function, environment variable reads, top-level orchestration
- Depends on: library modules in `app/comparator/` and `app/submitter/`
- Used by: AWS Lambda runtime

**Comparator library:**

- Purpose: Business logic for schema checking, comparison execution, S3 I/O
- Location: `app/comparator/` (excluding `lambdas/`)
- Contains: `schema_check.py`, `comparison.py`, `s3_utils.py`, `run_logging.py`, `comprehensive_sash_comparison.py`
- Depends on: boto3, pandas, cyvcf2/pysam (optional), bcftools (subprocess)
- Used by: Comparator handler, `scripts/run-comparator-local.py`

**Submitter library:**

- Purpose: OrcaBus integration — find or create a sash testdata run
- Location: `app/submitter/submit.py`
- Contains: `submit_sash_run()`, OrcaBus REST helpers, WruDraftValidator invocation, EventBridge emit
- Depends on: boto3, requests, SecretsManager, SSM
- Used by: Submitter handler

**Infrastructure (CDK):**

- Purpose: Define and deploy AWS resources
- Location: `infrastructure/`
- Contains: `stage/deployment-stack.ts` (Lambda + API GW + IAM), `stage/constants.ts`, `toolchain/stateless-stack.ts` (CodePipeline)
- Depends on: `@orcabus/platform-cdk-constructs`, `aws-cdk-lib`
- Used by: `bin/deploy.ts` CDK entrypoint

## Data Flow

### Comparator — Primary Request Path

1. Lambda invoked with `{"new_version": "0.7.0", "baseline_version": "0.6.4", "case_name": "SEQC-II-medium"}` (`app/comparator/lambdas/comparator/handler.py:210`)
2. Config YAML loaded from S3 (`handler.py:load_config` → `s3_utils.parse_s3_uri`)
3. Pairs filtered by `case_name` if provided (`handler.py:222`)
4. For each pair: both sash output dirs downloaded to `/tmp/<tmpdir>/baseline/` and `/tmp/<tmpdir>/new/` via `s3_utils.download_s3_dir` (`handler.py:134`)
5. Schema check run on each dir (`schema_check.check_schema` — validates 9 key sash output files) (`handler.py:137`)
6. If schema passes: `comparison.run_comparison` spawns `comprehensive_sash_comparison.py` as subprocess (`comparison.py:18`)
7. Comparison reads `summary.json` (preferred) or `metrics.json` from output dir (`comparison.py:39`)
8. All output files uploaded to `s3://${RESULT_S3_PREFIX}/${new}-vs-${baseline}/${case}/${exec_id}/test/data/` (`handler.py:155`)
9. Compact summary aggregated across pairs and returned as Lambda response (`handler.py:236`)

### Submitter — Submit or Skip Flow

1. Lambda invoked via API Gateway POST or direct invoke with `new_version`, `baseline_version`, library IDs (`handler.py:36`)
2. OrcaBus token fetched from SecretsManager; hostname from SSM (`submit.py:41-54`)
3. Workflow record looked up from OrcaBus REST API (`submit.py:85`)
4. Existing `umccr_tested_` runs queried by `codeVersion` + library IDs (`submit.py:102`)
5. If SUCCEEDED: return `already_succeeded`; if running: return `already_running`
6. Otherwise: build DRAFT payload, invoke WruDraftValidator Lambda, emit `SashRegressionRunSubmitted` event to EventBridge (`submit.py:212-216`)
7. Return `{"portal_run_id": ..., "action": "submitted"}` (`submit.py:218`)

**State Management:**

- No in-process state between Lambda invocations
- Module-level caches for OrcaBus token and hostname SSM value (within a single warm Lambda execution): `_token_cache`, `_hostname_cache` in `app/submitter/submit.py:37-38`

## Key Abstractions

**Pair:**

- Purpose: A tumor/normal library pair with baseline and new sash run S3 paths
- Examples: `config/testdata-cases.yaml` (YAML definition), parsed dict at `handler.py:pairs`
- Pattern: dict with keys `tumor`, `normal`, `run1` (baseline), `run2` (new), `metadata`

**Compact summary:**

- Purpose: Structured PASS/WARN/FAIL/MANUAL_CHECK rollup across all pairs
- Examples: returned by `handler._build_compact_summary`, logged as `FINAL_RESULT` structured log line
- Pattern: `{"status", "declared_update_type", "total_pairs", "pass_count", "fail_count", "critical_items", "warning_items", "metrics_impacted"}`

**workflowRunName encoding:**

- Purpose: Encodes both versions into the run name so the Watcher can extract baseline without DB lookup
- Pattern: `umccr_tested_sash_{new_ver}_vs_{baseline_ver}_{portal_run_id}` (`submit.py:67`)

## Entry Points

**Comparator Lambda:**

- Location: `app/comparator/lambdas/comparator/handler.py:handler`
- Triggers: Direct Lambda invocation
- Responsibilities: Load config, run per-pair comparison, return compact summary

**Submitter Lambda:**

- Location: `app/submitter/lambdas/submitter/handler.py:handler`
- Triggers: API Gateway POST (proxy integration) or direct Lambda invocation
- Responsibilities: Parse body, submit or skip sash regression run in OrcaBus

**CDK deploy entrypoint:**

- Location: `bin/deploy.ts`
- Triggers: `cdk deploy -c deployMode=<stateless|stateful|beta|prod|stage>`
- Responsibilities: Select correct stack class and target account/region

**Local development runner:**

- Location: `scripts/run-comparator-local.py`
- Triggers: Manual CLI invocation
- Responsibilities: Mirrors Lambda comparator flow against local `work/baseline/` and `work/new/` dirs without S3 I/O

## Architectural Constraints

- **Docker image:** Both Lambdas use the same Docker image (`app/`) — Submitter overrides `CMD` at deploy time via `DockerImageCode.fromImageAsset(..., {cmd: [...]})`. The default CMD is the Comparator handler (`infrastructure/stage/deployment-stack.ts:135`).
- **ephemeral storage:** Comparator allocates 10 GiB `/tmp` for downloading large sash output dirs; this is a hard Lambda limit and constrains how many pairs can run in one invocation.
- **bcftools dependency:** `comprehensive_sash_comparison.py` requires `bcftools` binary, compiled from source in the Dockerfile (`app/Dockerfile:4`). Not available from the AL2023 package manager.
- **Subprocess isolation:** The comparison script is run as a subprocess (not imported) to isolate heavy optional dependencies (pysam, cyvcf2) and prevent import-time failures from aborting the Lambda.
- **Global state:** Module-level token and hostname caches in `app/submitter/submit.py` are safe only because Lambda warm containers are single-threaded.
- **S3 path traversal guard:** `s3_utils.download_s3_dir` explicitly validates that each downloaded key stays within the target `local_dir` (`s3_utils.py:25`).

## Anti-Patterns

### Hardcoded WruDraftValidator function name

**What happens:** `getStageConstants()` in `infrastructure/stage/constants.ts:31` returns the same hardcoded WruDraftValidator function name for all stages.
**Why it's wrong:** Beta and prod environments need different function ARNs; a wrong name causes a silent deploy error when the Lambda cross-account invoke permission check fails.
**Do this instead:** Add a per-stage `wruDraftValidatorFunctionName` key to `getStackProps` / a stage-keyed config map, similar to how `testdataConfigS3Uri` will need per-stage values once prod is enabled.

### StatefulStack is a stub

**What happens:** `infrastructure/toolchain/stateful-stack.ts` contains unresolved TODO placeholders (`stack: this`, wrong `githubRepo`, wrong `stackName`).
**Why it's wrong:** Deploying `deployMode=stateful` would synthesize an invalid CDK pipeline.
**Do this instead:** Either implement stateful resources (if needed) or remove the stateful deploy mode until required.

## Error Handling

**Strategy:** Fail loudly at the pair level; continue with remaining pairs. Schema failure short-circuits comparison for that pair only. A failed comparison raises `RuntimeError` which propagates up and fails the entire Lambda invocation.

**Patterns:**

- Schema failure: logged at ERROR, pair result contains `{"schema": {..., "passed": false}, "comparison": null}` — no RuntimeError
- Comparison script non-zero exit with output present: treated as success with a warning log (`comparison.py:36`)
- Comparison script non-zero exit with no output: raises `RuntimeError` (`comparison.py:34`)
- OrcaBus REST errors: `requests.raise_for_status()` propagates `HTTPError` — Lambda fails and retries are caller's responsibility

## Cross-Cutting Concerns

**Logging:** Standard Python `logging` module; Lambda handler sets root logger to INFO. `run_logging.py` provides a `Tee` class that mirrors stdout/stderr to a `run.log` file for the comparison script.
**Validation:** Schema check (`schema_check.py`) validates file presence before comparison; input validation is minimal (required event keys only).
**Authentication:** OrcaBus REST API authenticated via JWT from SecretsManager (`orcabus/token-service-jwt`). AWS SDK uses Lambda execution role for all S3, SSM, EventBridge, and cross-Lambda calls.

---

_Architecture analysis: 2026-07-01_
