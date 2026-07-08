# External Integrations

**Analysis Date:** 2026-07-01

## APIs & External Services

**OrcaBus Workflow Manager (REST API):**

- Purpose: Query existing sash workflow runs by name and codeVersion; retrieve workflow metadata
- SDK/Client: `requests` (HTTP GET)
- Auth: Bearer JWT token fetched from Secrets Manager at runtime
- Base URL: dynamically resolved from SSM (`/hosted_zone/umccr/name`) as `https://workflow.<hostname>/`
- Endpoints used:
  - `GET /api/v1/workflow` — look up sash workflow by name + version
  - `GET /api/v1/workflowrun` — find existing regression runs
- Implementation: `app/submitter/submit.py` (`_get_workflow`, `_find_existing_run`)

**OrcaBus Metadata Manager (REST API):**

- Purpose: Resolve library IDs to OrcaBus internal IDs for workflow submission
- SDK/Client: `requests` (HTTP GET)
- Auth: Same Bearer JWT as Workflow Manager
- Base URL: `https://metadata.<hostname>/`
- Endpoints used:
  - `GET /api/v1/library` — look up library by libraryId
- Implementation: `app/submitter/submit.py` (`_get_library`)

**WruDraftValidator Lambda (internal AWS Lambda):**

- Purpose: Validate and persist a DRAFT workflow run event into OrcaBus — the official entry point for submitting sash runs
- SDK/Client: `boto3` Lambda client (`invoke` with `RequestResponse`)
- Auth: IAM role permission (`lambda:InvokeFunction`)
- Function name: per-stage constant in `infrastructure/stage/constants.ts` (`WRU_VALIDATOR_FUNCTION_NAME`)
- Env var: `WRU_VALIDATOR_LAMBDA_NAME`
- Implementation: `app/submitter/submit.py` (`_invoke_wru_validator`)

## Data Storage

**Databases:**

- None — no relational or document database. State is derived from OrcaBus APIs and S3 objects at runtime.

**S3 Buckets:**

- `test-data-503977275616-ap-southeast-2` — read-only testdata bucket; provides baseline reference data
  - Access: `s3:GetObject`, `s3:ListBucket` only
  - Managed by: UMCCR admin (manual promotion of validated results)
- `umccr-research-dev` — read/write results bucket; testdata config and comparison results stored here
  - Config key: `quentin/sash-regression/config/testdata-cases.yaml`
  - Results prefix: `sash-regression/<new_ver>-vs-<baseline_ver>/<case>/<exec_id>/test/`
  - Access: `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`
- `pipeline-*-cache-*` — pipeline cache buckets; read-only source of sash pipeline output files
- `project-data-*` — project data buckets; read-only source of sash pipeline output files (e.g. `project-wgs-accreditation`)
- SDK: `boto3` S3 client
- Implementation: `app/comparator/s3_utils.py`, `app/comparator/lambdas/comparator/handler.py`

**File Storage:**

- Lambda ephemeral storage: 10 GiB (`/tmp`) used for downloading sash output directories during comparison
- No persistent local filesystem

**Caching:**

- Module-level in-memory caching for OrcaBus token and hostname within a Lambda execution context (`_token_cache`, `_hostname_cache` in `app/submitter/submit.py`)

## Authentication & Identity

**OrcaBus JWT:**

- Secret name: `orcabus/token-service-jwt` (from `@orcabus/platform-cdk-constructs` shared config)
- Env var: `ORCABUS_TOKEN_SECRET_ID`
- Retrieval: `boto3` Secrets Manager client at runtime, cached in memory
- Implementation: `app/submitter/submit.py` (`_orcabus_token`)

**IAM Role:**

- Single shared Lambda execution role created in `infrastructure/stage/deployment-stack.ts`
- Grants: S3 read/write on specific buckets, Secrets Manager read, SSM read, Lambda invoke (WruDraftValidator), EventBridge PutEvents
- Both comparator and submitter Lambda functions share this role

## Messaging & Events

**AWS EventBridge:**

- Bus: `OrcaBusMain` (from `@orcabus/platform-cdk-constructs` shared config)
- Env var: `EVENTS_BUS_NAME`
- Events emitted by submitter:
  - Source: `sash-regression.submitter`
  - DetailType: `SashRegressionRunSubmitted`
  - Detail fields: `portalRunId`, `newVersion`, `baselineVersion`, `workflowRunName`, `tumorLibraryId`, `normalLibraryId`
- SDK: `boto3` events client (`put_events`)
- Implementation: `app/submitter/submit.py` (`_emit_submitted_event`)

## AWS Parameter Store (SSM)

- Parameter: `/hosted_zone/umccr/name` — OrcaBus API hostname
- Env var: `HOSTNAME_SSM_PARAMETER_NAME`
- SDK: `boto3` SSM client (`get_parameter`)
- Implementation: `app/submitter/submit.py` (`_hostname`)

## Monitoring & Observability

**Error Tracking:**

- None — no external error tracking service (e.g. Sentry)

**Logs:**

- Python: `logging` module at INFO level, structured with `logger.info()`; final result logged as `FINAL_RESULT <json>` for easy grep
- IaC: CDK stdout during synth/deploy
- Lambda logs go to CloudWatch Logs via the managed `AWSLambdaBasicExecutionRole` policy

## CI/CD & Deployment

**Hosting:**

- AWS Lambda (ARM64, Docker image runtime), `ap-southeast-2`
- API Gateway (REST) fronts the submitter Lambda (`LambdaRestApi` with proxy=true)

**CI Pipeline:**

- GitHub Actions: `.github/workflows/pr-tests.yml`
  - Jobs: `pre-commit-lint-security` (ESLint, Prettier, pre-commit, TruffleHog), `test-iac` (Jest CDK tests), `test-app` (pytest)
  - Runners: `ubuntu-latest` (lint), `ubuntu-22.04-arm` (tests)
- AWS CodePipeline: `DeploymentStackPipeline` CDK construct in `infrastructure/toolchain/stateless-stack.ts`
  - Triggered by pushes to `main` branch of `service-sash-regression` GitHub repo
  - Stages: BETA → GAMMA → PROD
  - Pre-deploy: `cd app && make install && make check && make test`

**Secret Scanning:**

- TruffleHog OSS (GitHub Actions, PR checks)
- detect-secrets with baseline: `.secrets.baseline`
- Pre-commit hooks: `detect-aws-credentials`, `detect-private-key`

## Webhooks & Callbacks

**Incoming:**

- Submitter Lambda exposed via API Gateway REST endpoint (proxy integration); accepts POST with JSON body
- Comparator Lambda invoked directly (manual invocation only — no API Gateway trigger)

**Outgoing:**

- OrcaBus Workflow Manager REST API
- OrcaBus Metadata Manager REST API
- WruDraftValidator Lambda (synchronous invoke)
- EventBridge PutEvents to OrcaBusMain bus

---

_Integration audit: 2026-07-01_
