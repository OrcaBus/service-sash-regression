# Submitting a New Sash Version for Regression Testing

- Version: 2026.07.08
- Contact: Quentin Clayssen

- [Introduction](#introduction)
- [Requirements](#requirements)
- [Procedure](#procedure)
  - [1. Find the Submitter API endpoint](#1-find-the-submitter-api-endpoint)
  - [2. Submit via API Gateway](#2-submit-via-api-gateway)
  - [3. Submit via direct Lambda invocation](#3-submit-via-direct-lambda-invocation)
  - [4. Understand the response](#4-understand-the-response)
  - [5. Run the comparison once the sash run completes](#5-run-the-comparison-once-the-sash-run-completes)
- [Confirmation](#confirmation)

## Introduction

The Submitter Lambda submits a new sash regression run to OrcaBus. It checks whether a run for the same version and library IDs already exists — if one has already SUCCEEDED or is currently running, it skips submission and returns the existing state. Otherwise it builds a DRAFT payload, validates it via the WruDraftValidator Lambda, and emits a `SashRegressionRunSubmitted` event to EventBridge.

This SOP covers submitting a new sash version (e.g. `0.7.0`) to be run against the default testdata libraries.

## Requirements

- AWS CLI v2 installed and configured
- AWS profile with API Gateway invoke permissions (e.g. `umccr-dev-pu` for beta)
- `curl` or `aws lambda invoke` available
- The new sash version must already be registered in OrcaBus Workflow Manager
- `jq` for formatting JSON output (optional but recommended)

> **Convenience script:** A shell script that resolves the Submitter API URL from CloudFormation outputs and handles the full submission flow with a confirmation prompt is available at [`../SR.1/generate-WRU-draft.sh`](../SR.1/generate-WRU-draft.sh). See [`../SR.1/SR.1-SubmitRegressionRun.md`](../SR.1/SR.1-SubmitRegressionRun.md) for usage.

## Procedure

### 1. Find the Submitter API endpoint

```sh
# List API Gateways and find the SashRegression one
aws apigateway get-rest-apis \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  --query 'items[?contains(name,`SashRegression`)].{name:name,id:id}' \
  --output table
```

The endpoint URL follows the pattern:

```
https://<api-id>.execute-api.ap-southeast-2.amazonaws.com/prod/
```

### 2. Submit via API Gateway

Submit `new_version` for regression testing against `baseline_version` using the default testdata library IDs (`L2301218` / `L2301217`):

```sh
curl -X POST \
  "https://<api-id>.execute-api.ap-southeast-2.amazonaws.com/prod/" \
  -H "Content-Type: application/json" \
  -d '{
    "new_version": "0.7.0",
    "baseline_version": "0.6.4",
    "tumor_library_id": "L2301218",
    "normal_library_id": "L2301217"
  }'
```

> **Note:** `tumor_library_id` and `normal_library_id` default to `L2301218` / `L2301217` (set via Lambda env vars `TESTDATA_TUMOR_LIBRARY_ID` / `TESTDATA_NORMAL_LIBRARY_ID`) so they can be omitted for the standard testdata case.

### 3. Submit via direct Lambda invocation

Use this when API Gateway access is restricted or for scripting:

```sh
# Find the Submitter function name
aws lambda list-functions \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  --query 'Functions[?contains(FunctionName,`SashRegression`) && contains(FunctionName,`Submitter`)].FunctionName' \
  --output text

# Invoke directly
aws lambda invoke \
  --function-name <function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  /tmp/submit-response.json && cat /tmp/submit-response.json | jq .
```

### 4. Understand the response

| `action` value      | Meaning                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `submitted`         | New run successfully submitted to OrcaBus — note the `portal_run_id`    |
| `already_succeeded` | A run for this version + libraries already SUCCEEDED — no action needed |
| `already_running`   | A run is currently in progress — check OrcaBus Portal for status        |

Example success response:

```json
{
  "action": "submitted",
  "portal_run_id": "20260708abcd1234"
}
```

The submitted run will have `workflowRunName`:

```
umccr_tested_sash_0.7.0_vs_0.6.4_<portal_run_id>
```

### 5. Run the comparison once the sash run completes

The submission only queues the sash run in OrcaBus. The Comparator Lambda must be invoked separately once the run has SUCCEEDED. Follow [PM.SR.1](../PM.SR.1/PM.SR.1-ManualComparatorInvocation.md) to invoke the Comparator.

> **Future:** A Watcher Lambda will automate this step — polling OrcaBus for run completion and triggering the Comparator automatically.

## Confirmation

1. Verify the submission in the OrcaBus Portal — search for the `workflowRunName` or `portal_run_id` returned in the response.
2. Check CloudWatch logs for the Submitter Lambda to confirm no errors:

```sh
aws logs tail /aws/lambda/<submitter-function-name> --follow --profile umccr-dev-pu
```
