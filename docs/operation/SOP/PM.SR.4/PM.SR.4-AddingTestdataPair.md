# Adding a New Testdata Pair

- Version: 2026.07.08
- Contact: Quentin Clayssen

- [Introduction](#introduction)
- [Requirements](#requirements)
- [Procedure](#procedure)
  - [1. Identify the sash output S3 paths](#1-identify-the-sash-output-s3-paths)
  - [2. Edit testdata-cases.yaml](#2-edit-testdata-casesyaml)
  - [3. Upload the updated config to S3](#3-upload-the-updated-config-to-s3)
  - [4. Verify the new pair runs cleanly](#4-verify-the-new-pair-runs-cleanly)
- [Confirmation](#confirmation)

## Introduction

The Comparator Lambda reads its test case definitions from `config/testdata-cases.yaml` (synced to S3). Each entry in `pairs` defines a tumor/normal library pair with S3 paths to the baseline (`run1`) and new-version (`run2`) sash output directories.

This SOP covers adding a new tumor/normal pair to the comparison config.

## Requirements

- AWS CLI v2 with read access to the sash pipeline cache buckets and write access to `umccr-research-dev`
- The sash run must have already completed successfully — the output S3 prefix must exist and contain all expected output files
- Profiles: `umccr-dev-pu` (dev) or `umccr-prod-operator` (prod)

## Procedure

### 1. Identify the sash output S3 paths

Locate the completed sash run output directories in S3. These are typically under:

```
s3://pipeline-prod-cache-503977275616-ap-southeast-2/byob-icav2/production/sash/<portal_run_id>/
s3://project-wgs-accreditation-<account>/...
```

Verify the output directory contains the required sash files (schema check expects these 9):

```sh
aws s3 ls s3://<bucket>/<prefix>/<TumorId>__<NormalId>/ --profile umccr-dev-pu --recursive \
  | awk '{print $4}' \
  | grep -E "\.(vcf\.gz|tsv\.gz|json)$" \
  | head -20
```

Note the full S3 prefix for both the baseline run (`run1`) and the new version run (`run2`), including the trailing `/`.

### 2. Edit testdata-cases.yaml

Add a new entry to `config/testdata-cases.yaml`:

```yaml
pairs:
  # ... existing pairs ...

  - tumor: <TUMOR_LIBRARY_ID> # e.g. L2400001
    normal: <NORMAL_LIBRARY_ID> # e.g. L2400002
    run1: s3://<bucket>/<prefix-for-baseline>/ # trailing slash required
    run2: s3://<bucket>/<prefix-for-new-version>/ # trailing slash required
    metadata:
      subject: <SUBJECT_ID> # e.g. SBJ00999
      case: <CASE_NAME> # used as case_name in Lambda invocation, e.g. MY-SAMPLE
      cohort: <COHORT> # e.g. SEQC-II
      run1_portal_run_id: <PORTAL_RUN_ID_BASELINE>
      run2_portal_run_id: <PORTAL_RUN_ID_NEW>
```

> **Important:** The `case` value under `metadata` is what you pass as `case_name` when invoking the Comparator Lambda. Keep it short and descriptive — no spaces (use hyphens).

Update `alias_run1` and `alias_run2` at the top of the file if the version labels have changed:

```yaml
alias_run1: 'sash 0.6.4'
alias_run2: 'sash 0.7.0'
```

### 3. Upload the updated config to S3

The Comparator Lambda reads the config fresh on every invocation — no Lambda redeployment needed.

```sh
# Upload to dev/beta
aws s3 cp config/testdata-cases.yaml \
  s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
  --profile umccr-dev-pu

# Verify the upload
aws s3 cp \
  s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
  - --profile umccr-dev-pu
```

Commit the local `config/testdata-cases.yaml` change to the repo so the config stays in version control.

### 4. Verify the new pair runs cleanly

Invoke the Comparator Lambda with just the new case to confirm it works before running all pairs:

```sh
aws lambda invoke \
  --function-name <comparator-function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4","case_name":"<YOUR-NEW-CASE>"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  /tmp/new-pair-test.json && cat /tmp/new-pair-test.json | jq .
```

A schema failure (`"schema": {"passed": false}`) means the S3 path is wrong or the run is incomplete — check that all 9 required output files are present at the specified prefix.

See [PM.SR.1](../PM.SR.1/PM.SR.1-ManualComparatorInvocation.md) for full invocation details.

## Confirmation

The new pair returns a result entry in the Lambda response. Check the `schema` and `comparison` fields for the new pair in the response JSON. Full results are written to:

```
s3://umccr-research-dev/sash-regression/<new_version>-vs-<baseline_version>/<case>/<exec_id>/test/
```
