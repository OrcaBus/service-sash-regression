# Manually Invoking a Regression Comparison

- Version: 2026.07.08
- Contact: Quentin Clayssen

- [Introduction](#introduction)
- [Requirements](#requirements)
- [Procedure](#procedure)
  - [1. Find the Comparator function name](#1-find-the-comparator-function-name)
  - [2. Check the active config](#2-check-the-active-config)
  - [3. Invoke the Lambda](#3-invoke-the-lambda)
  - [4. Review results](#4-review-results)
- [Local invocation (without Docker)](#local-invocation-without-docker)
- [Confirmation](#confirmation)

## Introduction

The Comparator Lambda compares `sash` pipeline outputs between a new version and a baseline version for one or all configured test cases. It downloads sash output directories from S3, runs a schema check and a comprehensive comparison, uploads results to S3, and returns a compact PASS/FAIL/MANUAL_CHECK summary.

This SOP covers manually invoking the Comparator Lambda — either against the deployed Lambda in AWS, or locally via Python without Docker.

## Requirements

- AWS CLI v2 installed and configured
- AWS profile with invoke permissions on the Comparator Lambda (e.g. `umccr-dev-pu` for beta)
- `jq` for formatting JSON output (optional but recommended)
- The sash output S3 paths for both `new_version` and `baseline_version` must already exist in the configured S3 locations (`testdata-cases.yaml`)

## Procedure

### 1. Find the Comparator function name

CDK appends a hash to the logical resource name — the name is not stable across deploys:

```sh
aws lambda list-functions \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  --query 'Functions[?contains(FunctionName,`SashRegression`) && contains(FunctionName,`Comparator`)].FunctionName' \
  --output text
```

### 2. Check the active config

Verify which test cases and S3 paths the Lambda will use:

```sh
# Get the config S3 URI from the Lambda environment
CONFIG_URI=$(aws lambda get-function-configuration \
  --function-name <function-name> \
  --profile umccr-dev-pu \
  --query 'Environment.Variables.TESTDATA_CONFIG_S3_URI' \
  --output text)

# Print the config
aws s3 cp "${CONFIG_URI}" - --profile umccr-dev-pu
```

The `case` field under `metadata` in each pair is what you pass as `case_name` below.

### 3. Invoke the Lambda

**Run all pairs (omit `case_name`):**

```sh
aws lambda invoke \
  --function-name <function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  /tmp/response.json && cat /tmp/response.json | jq .
```

**Run a single pair by case name:**

```sh
aws lambda invoke \
  --function-name <function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4","case_name":"SEQC-II-medium"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  /tmp/response.json && cat /tmp/response.json | jq .
```

> **Note:** The Comparator has a 15-minute timeout. For a single pair with large sash outputs, expect 5–12 minutes.

### 4. Review results

The Lambda response is a compact summary:

```json
{
  "status": "PASS",
  "declared_update_type": "patch",
  "total_pairs": 1,
  "pass_count": 1,
  "fail_count": 0,
  "manual_check_count": 0,
  "critical_count": 0,
  "critical_items": [],
  "metrics_impacted": false
}
```

Full comparison results (per-file diffs, VCF counts, Purple metrics) are uploaded to S3:

```
s3://umccr-research-dev/sash-regression/<new_version>-vs-<baseline_version>/<case>/<exec_id>/test/
```

Get the result prefix from the Lambda environment variable `RESULT_S3_PREFIX`:

```sh
aws lambda get-function-configuration \
  --function-name <function-name> \
  --profile umccr-dev-pu \
  --query 'Environment.Variables.RESULT_S3_PREFIX' \
  --output text
```

## Local invocation (without Docker)

Use this when iterating quickly against real S3 data without building the Docker image.

Requires the Python venv to be activated and AWS credentials available in the environment.

```sh
cd app
make install   # first time only

make invoke-local
# or with a specific case:
AWS_PROFILE=umccr-dev-pu \
TESTDATA_CONFIG_S3_URI=s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
RESULT_S3_PREFIX=s3://umccr-research-dev/quentin/sash-regression/results \
python -c "
from comparator.lambdas.comparator.handler import handler
import json
print(json.dumps(handler({'new_version':'0.7.0','baseline_version':'0.6.4','case_name':'SEQC-II-medium'}, None), indent=2, default=str))
"
```

## Confirmation

A successful invocation returns `"status": "PASS"` in the Lambda response and writes result files to the `RESULT_S3_PREFIX` path. `FAIL` means a real difference was detected (there is no tolerance band — see [`docs/comparison-thresholds.md`](../../../comparison-thresholds.md)); `MANUAL_CHECK` means the comparison could not decide on its own. Check the CloudWatch log group for `FINAL_RESULT` log lines if the invocation fails or returns unexpected status.

```sh
# Tail the most recent log stream
aws logs tail /aws/lambda/<function-name> --follow --profile umccr-dev-pu
```
