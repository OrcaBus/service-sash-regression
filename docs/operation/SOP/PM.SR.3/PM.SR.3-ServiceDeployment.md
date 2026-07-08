# Deploying a New Version of the Sash Regression Service

- Version: 2026.07.08
- Contact: Quentin Clayssen

- [Introduction](#introduction)
- [Requirements](#requirements)
- [Procedure](#procedure)
  - [1. Make and verify changes locally](#1-make-and-verify-changes-locally)
  - [2. Push to main — automated pipeline](#2-push-to-main--automated-pipeline)
  - [3. Manual deploy to beta (if needed)](#3-manual-deploy-to-beta-if-needed)
  - [4. Manual deploy to prod (if needed)](#4-manual-deploy-to-prod-if-needed)
- [Updating the testdata config in S3](#updating-the-testdata-config-in-s3)
- [Confirmation](#confirmation)

## Introduction

The service is deployed via AWS CodePipeline (`DeploymentStackPipeline`). Pushing to `main` automatically triggers a pipeline run that deploys to `beta` then `prod`. Manual CDK deploys are available for emergency use or local dev iteration.

Both Lambdas (Comparator and Submitter) are built from the same Docker image in `app/`. A code change to either requires a new image build and Lambda update.

## Requirements

- Node.js 22.9.0+, pnpm 10.30.2 (`corepack enable pnpm`)
- Docker Desktop running (required for CDK tests and manual CDK deploy)
- AWS CLI v2 with appropriate profiles:
  - `umccr-dev-pu` — beta environment (dev account)
  - `umccr-prod-admin` — prod environment (prod account)
- Pre-commit hooks installed: `pre-commit install`

## Procedure

### 1. Make and verify changes locally

```sh
# Install dependencies
make install

# Run all checks (audit, prettier, eslint, pre-commit, ruff)
make check-all

# Run CDK tests (requires Docker Desktop)
pnpm test

# Run Python unit tests
cd app && make test
```

All checks must pass before pushing. The CI pipeline (`pr-tests.yml`) runs the same checks on every PR.

### 2. Push to main — automated pipeline

Merge your PR to `main`. The CodePipeline in the toolchain account will:

1. Build the Docker image from `app/`
2. Run `cd app && make install && make check && make test`
3. Deploy `SashRegressionStack` to **beta** (`ap-southeast-2`)
4. Deploy `SashRegressionStack` to **prod** (`ap-southeast-2`)

Monitor the pipeline in the AWS Console under CodePipeline in the toolchain account.

### 3. Manual deploy to beta (if needed)

Use this for rapid iteration in the dev environment without going through the full pipeline.

```sh
# List all stacks
pnpm cdk-stateless ls

# Deploy the toolchain pipeline stack (bastion account)
pnpm cdk-stateless deploy -e OrcaBusStatelessSashRegressionStack

# Deploy the application stack directly to beta (dev account)
pnpm cdk-beta deploy SashRegressionStack
```

### 4. Manual deploy to prod (if needed)

```sh
pnpm cdk-prod deploy SashRegressionStack
```

> **Warning:** Direct prod deploys bypass the CodePipeline and skip the beta verification stage. Only use in emergencies with explicit approval.

## Updating the testdata config in S3

`config/testdata-cases.yaml` is read by the Comparator Lambda at runtime from S3. After editing the file locally, upload it manually:

```sh
# Upload to dev (used by beta Lambda)
aws s3 cp config/testdata-cases.yaml \
  s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
  --profile umccr-dev-pu

# Upload to prod (if TESTDATA_CONFIG_S3_URI differs in prod)
aws s3 cp config/testdata-cases.yaml \
  s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
  --profile umccr-prod-operator
```

> The Comparator Lambda reads the config fresh on every invocation — no Lambda redeployment needed after a config change.

## Confirmation

After a successful deploy, verify the Lambda is running the expected image:

```sh
# Check the deployed image digest and last update time
aws lambda get-function \
  --function-name <comparator-function-name> \
  --profile umccr-dev-pu \
  --query 'Configuration.{LastModified:LastModified,CodeSize:CodeSize}' \
  --output table
```

Run a smoke-test invocation with a single case to confirm the new code is working:

```sh
# See PM.SR.1 for full invocation instructions
aws lambda invoke \
  --function-name <comparator-function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4","case_name":"SEQC-II-medium"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  --region ap-southeast-2 \
  /tmp/smoke-test.json && cat /tmp/smoke-test.json | jq .status
```
