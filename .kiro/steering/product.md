# Product: Sash Regression Service

## Summary

This is an OrcaBus microservice that validates `sash` bioinformatics pipeline releases by comparing outputs from a new version against a curated baseline. It catches regressions before a release is promoted to production.

## Core Responsibilities

- **Compare** sash pipeline outputs (VCFs, TSVs, Purple metrics, BCFtools stats) between a new version and a baseline version for a set of known test cases
- **Schema-check** that all 9 required sash output files are present before running comparison
- **Submit** new sash regression runs to OrcaBus (via WruDraftValidator + EventBridge) and skip if one already exists or has succeeded
- **Watch** for SUCCEEDED sash WorkflowRunStateChange events and automatically trigger the Comparator
- **Report** a structured PASS/WARN/FAIL/MANUAL_CHECK rollup across all test case pairs, uploaded to S3

## Three Lambdas

```
Comparator Lambda  (direct invoke or async from Watcher)
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

Watcher Lambda  (EventBridge rule — automatic)
  → receives WorkflowRunStateChange events for workflowRunName prefix 'umccr_tested_'
  → parse_run_name() extracts new_version + baseline_version from run name
  → on SUCCEEDED: resolves output path (engineParameters.outputUri + outputs.sashRelPath)
  → async-invokes Comparator with new_output_path
  → on FAILED: logs warning, no action
```

## End-to-End Event Flow

```
POST /submit  (or direct invoke Submitter)
  → Submitter Lambda
  → OrcaBus WruDraftValidator  (payload.data seeded from the prior SUCCEEDED sash run's inputs — fixed, see Issue #5)
  → SashRegressionRunSubmitted event on OrcaBusMain
  → OrcaBus Workflow Manager creates WorkflowRun, runs sash pipeline
  → WorkflowRunStateChange SUCCEEDED event on OrcaBusMain
  → Watcher Lambda triggered by EventBridge rule
  → Comparator Lambda invoked async with new_output_path
  → Results uploaded to s3://umccr-research-dev/sash-regression/...
```

## Upstream / Downstream

- **Upstream**: Manual invocation via Submitter API, or Watcher triggered by OrcaBus EventBridge
- **Downstream**: OrcaBus Workflow Manager (run tracking), S3 results bucket (`umccr-research-dev`)
- **Key dependencies**: OrcaBus Workflow Manager REST API, OrcaBus Metadata Manager REST API, WruDraftValidator Lambda, EventBridge `OrcaBusMain`

## Environments

Deploys to `beta` and `prod` via AWS CodePipeline. The toolchain account hosts the CodePipeline; the application Lambda stacks deploy cross-account to dev/prod. A `gamma` manual deploy mode is also available via `pnpm cdk-gamma`.
