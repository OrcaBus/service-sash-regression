# Product: Sash Regression Service

## Summary

This is an OrcaBus microservice that validates `sash` bioinformatics pipeline releases by comparing outputs from a new version against a curated baseline. It catches regressions before a release is promoted to production.

## Core Responsibilities

- **Compare** sash pipeline outputs (VCFs, TSVs, Purple metrics, BCFtools stats) between a new version and a baseline version for a set of known test cases
- **Schema-check** that all 9 required sash output files are present before running comparison
- **Submit** new sash regression runs to OrcaBus (via WruDraftValidator + EventBridge) and skip if one already exists or has succeeded
- **Report** a structured PASS/WARN/FAIL/MANUAL_CHECK rollup across all test case pairs, uploaded to S3

## Two Lambdas

```
Comparator Lambda  (direct invoke)
  → load test-case config from S3
  → for each pair: download sash outputs, schema check, run comparison
  → upload results to S3
  → return compact PASS/WARN/FAIL summary

Submitter Lambda  (API Gateway POST or direct invoke)
  → check OrcaBus for existing run (skip if SUCCEEDED or running)
  → build DRAFT payload
  → invoke WruDraftValidator
  → emit SashRegressionRunSubmitted to EventBridge
  → return { action, portal_run_id }
```

## Event Flow

```
POST /submit  (or direct invoke)
  → Submitter Lambda
  → OrcaBus WruDraftValidator
  → SashRegressionRunSubmitted event on OrcaBusMain
  → [future: Watcher Lambda polls OrcaBus until run completes]
  → [future: Comparator Lambda triggered automatically]
```

## Upstream / Downstream

- **Upstream**: Manual invocation or future Watcher Lambda
- **Downstream**: OrcaBus Workflow Manager (run tracking), S3 results bucket (`umccr-research-dev`)
- **Key dependencies**: OrcaBus Workflow Manager REST API, OrcaBus Metadata Manager REST API, WruDraftValidator Lambda, EventBridge `OrcaBusMain`

## Environments

Deploys to `beta` and `prod` via AWS CodePipeline. The toolchain account hosts the CodePipeline; the application Lambda stacks deploy cross-account to dev/prod.
