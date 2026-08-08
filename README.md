Sash Regression Service
================================================================================

- [Sash Regression Service](#sash-regression-service)
  - [New Here? Start Here](#new-here-start-here)
  - [Service Description](#service-description)
    - [Name \& responsibility](#name--responsibility)
    - [Description](#description)
    - [API Endpoints](#api-endpoints)
    - [Consumed Events](#consumed-events)
    - [Published Events](#published-events)
    - [(Internal) Data states \& persistence model](#internal-data-states--persistence-model)
    - [Major Business Rules](#major-business-rules)
    - [Permissions \& Access Control](#permissions--access-control)
    - [Change Management](#change-management)
      - [Versioning strategy](#versioning-strategy)
      - [Release management](#release-management)
  - [Infrastructure \& Deployment](#infrastructure--deployment)
    - [Stateful](#stateful)
    - [Stateless](#stateless)
    - [CDK Commands](#cdk-commands)
    - [Stacks](#stacks)
  - [Development](#development)
    - [Project Structure](#project-structure)
    - [Setup](#setup)
      - [Requirements](#requirements)
      - [Install Dependencies](#install-dependencies)
    - [Conventions](#conventions)
    - [Linting \& Formatting](#linting--formatting)
    - [Testing](#testing)
  - [Glossary \& References](#glossary--references)


New Here? Start Here
--------------------------------------------------------------------------------

- [`docs/HANDOVER.md`](docs/HANDOVER.md) — **start here if you are taking this service over.**
  What is deployed, what is not, open items, and how to reproduce a comparison from scratch.
- [`docs/beginner-guide.md`](docs/beginner-guide.md) — if you are not familiar with AWS, Lambda,
  or CDK, read this first.
- [`docs/operation/SOP/`](docs/operation/SOP/README.md) — the runbooks: manual Comparator
  invocation, submitting a regression run, deploying, adding a testdata pair, troubleshooting.
- [`docs/comparison-thresholds.md`](docs/comparison-thresholds.md) — how `PASS` / `FAIL` is
  decided, and why there is no tolerance band.


Service Description
--------------------------------------------------------------------------------

### Name & responsibility

**Sash Regression** — compares `sash` pipeline outputs between a new version and a baseline version, to catch regressions before a release.

### Description

The service is three Docker-based Lambdas that chain into one regression run:

1. **Submitter** — given a new and a baseline `sash` version, checks OrcaBus for an existing
   matching run and, if there isn't one, submits a new `sash` run via the `WruDraftValidator`
   Lambda. Test runs are named `umccr_tested_sash_{new}_vs_{baseline}_{portal_run_id}` so they
   are identifiable without a database lookup.
2. **Watcher** — listens for the `sash` run finishing and asynchronously invokes the Comparator
   with the new run's output path.
3. **Comparator** — downloads the new and baseline output directories from S3, runs a schema
   check and a comprehensive comparison between the two trees (`app/comparator/schema_check.py`,
   `app/comparator/comparison.py`, `app/comparator/comprehensive_sash_comparison.py`), uploads
   the results to the results bucket, and returns a compact `PASS` / `FAIL` / `MANUAL_CHECK`
   summary. See [`docs/comparison-thresholds.md`](docs/comparison-thresholds.md) for how that
   verdict is decided.

The set of test cases and their S3 locations is driven by a YAML config file
(`testdata/config/sash-regression/testdata-cases.yaml`) read from the testdata bucket.

The Comparator can also be invoked on its own against two already-completed runs, without the
Submitter or Watcher — see [`docs/operation/SOP/PM.SR.1/`](docs/operation/SOP/PM.SR.1/PM.SR.1-ManualComparatorInvocation.md).

**Phase 3 (Publisher — post results to GitHub and Slack) is designed but not implemented.** See
`.kiro/specs/sash-regression-completion/`.

### API Endpoints

The Submitter is fronted by an API Gateway proxy (`SubmitterApi`), whose URL is published as the
`SubmitterApiUrl` stack output. `POST` to it with:

```json
{
  "new_version": "0.7.0",
  "baseline_version": "0.6.4",
  "tumor_library_id": "L2301218",
  "normal_library_id": "L2301217"
}
```

`new_version` and `baseline_version` are required; the two library IDs default to the testdata
pair configured on the Lambda (`TESTDATA_TUMOR_LIBRARY_ID` / `TESTDATA_NORMAL_LIBRARY_ID`). The
response carries `portal_run_id` and an `action` of `submitted`, `already_running`, or
`already_succeeded`. In practice this endpoint is called by
[`docs/operation/SOP/SR.1/generate-WRU-draft.sh`](docs/operation/SOP/SR.1/generate-WRU-draft.sh)
rather than by hand.

The Comparator has no endpoint — it is invoked by the Watcher, or directly for a manual run:

```json
{ "new_version": "0.7.0", "baseline_version": "0.6.4" }
```

### Consumed Events

| Source | Detail type | Filter |
|--------|-------------|--------|
| `orcabus.workflowmanager` | `WorkflowRunStateChange` | `detail.workflowRunName` prefix `umccr_tested_` |

The Watcher receives every state change for our test runs and acts only on `SUCCEEDED` (it
invokes the Comparator) and `FAILED` (it logs a warning). Anything whose run name does not parse
as one of ours is ignored.

### Published Events

| Source | Detail type | Emitted by |
|--------|-------------|------------|
| `sash-regression.submitter` | `SashRegressionRunSubmitted` | Submitter, after a run is submitted |

Detail: `portalRunId`, `newVersion`, `baselineVersion`, `workflowRunName`, `tumorLibraryId`,
`normalLibraryId`. Nothing consumes this event yet — it exists for observability and for the
Phase 3 Publisher.

### (Internal) Data states & persistence model

This service is stateless. It reads pipeline outputs and baseline config from S3 and writes comparison results back to S3 — no database is used.

| Bucket | Purpose | Access |
|--------|---------|--------|
| `pipeline-*-cache-*`, `project-data-*` | Source `sash` pipeline outputs to compare | Read-only |
| `test-data-503977275616-ap-southeast-2` (testdata) | Baseline reference config/data | Read-only — never written to by this service |
| `umccr-research-dev` (results) | Comparison results, all stages | Write |

### Major Business Rules

- The testdata bucket is treated as a read-only, curated baseline. Comparison results always go to `umccr-research-dev`, regardless of which stage (`beta`/`prod`) the Lambda runs in — promoting a result to the testdata baseline is a manual, one-way admin action.
- A path-traversal guard is enforced when downloading S3 directories (`app/comparator/s3_utils.py`).
- There is no tolerance band on the comparison. Any difference in a key clinical output file, and any purity/ploidy/TMB/MSI delta above floating-point noise, fails the pair — see [`docs/comparison-thresholds.md`](docs/comparison-thresholds.md).
- Test runs are named `umccr_tested_sash_{new}_vs_{baseline}_{portal_run_id}`. The prefix is what the Watcher's EventBridge rule filters on, so it is load-bearing, not just a label.
- The Submitter is idempotent by lookup: it checks OrcaBus for an existing run matching the same code version and libraries before submitting, and returns `already_running` / `already_succeeded` instead of creating a duplicate.

### Permissions & Access Control

No end-user authentication or authorisation applies — note that the Submitter API Gateway endpoint is unauthenticated, and is relied on being non-public knowledge rather than access-controlled. Each Lambda has its own IAM role scoped to what it needs (`infrastructure/stage/deployment-stack.ts`): the Comparator to the S3 buckets listed above, the Submitter to the OrcaBus token secret, the hostname SSM parameter, the `WruDraftValidator` function, and `events:PutEvents` on the main bus, and the Watcher to invoking the Comparator.

### Change Management

#### Versioning strategy

Manual tagging of git commits following Semantic Versioning (semver) guidelines.

#### Release management

The service employs a fully automated CI/CD pipeline that automatically builds and releases all changes to the `main` branch across `beta` and `prod` environments. Manual CDK deploys are available for dev iteration — see [`docs/operation/SOP/PM.SR.3/`](docs/operation/SOP/PM.SR.3/PM.SR.3-ServiceDeployment.md).

> As of 2026-08-07 the service has only ever been deployed to **beta**, and the `WruDraftValidator` function name for gamma/prod is still a beta placeholder in `infrastructure/stage/constants.ts`. Resolve that before relying on a prod deploy.


Infrastructure & Deployment
--------------------------------------------------------------------------------

Infrastructure is managed via CDK. This template provides two types of CDK entry points: `cdk-stateless` and `cdk-stateful`.

### Stateful

This service has no stateful resources. The `StatefulStack` is kept as a placeholder.

### Stateless

All three Lambdas are ARM64 Docker image functions built from the same `./app` image, each with a
different `cmd` entrypoint and its own dedicated IAM role.

- **`ComparatorFunction`** — 4096 MB, 10 GiB ephemeral storage, 15 min timeout. Runs the schema
  check and comparison logic, reading `TESTDATA_CONFIG_S3_URI` and writing to `RESULT_S3_PREFIX`.
- **`SubmitterFunction`** — 512 MB, 5 min timeout. Reads the OrcaBus token from Secrets Manager
  and the API hostname from SSM, invokes the `WruDraftValidator` Lambda, and emits
  `SashRegressionRunSubmitted`. Fronted by **`SubmitterApi`**, a `LambdaRestApi` proxy whose URL
  is exported as the `SubmitterApiUrl` stack output.
- **`WatcherFunction`** — 512 MB, 5 min timeout. Triggered by **`WatcherRule`**, an EventBridge
  rule on the OrcaBus main bus matching `WorkflowRunStateChange` events whose `workflowRunName`
  starts with `umccr_tested_`. Async-invokes the Comparator on `SUCCEEDED`.

The `WruDraftValidator` function name is per-stage in `infrastructure/stage/constants.ts`.
**Only the BETA name is known** — GAMMA and PROD currently hold the BETA placeholder, so those
stages are not deployable as-is.

### CDK Commands

You can access CDK commands using the `pnpm` wrapper script.

- **`cdk-stateless`**: Used to deploy stacks containing stateless resources (e.g., AWS Lambda), which can be easily redeployed without side effects.
- **`cdk-stateful`**: Used to deploy stacks containing stateful resources (e.g., AWS DynamoDB, AWS RDS), where redeployment may not be ideal due to potential side effects.

The type of stack to deploy is determined by the context set in the `./bin/deploy.ts` file.

All deployments go through the `DeploymentStackPipeline` construct, which handles cross-account role assumptions and applies the correct per-environment configuration from `config.ts`. Use the pipeline sub-stack path shown below.

Pattern:
```sh
# Deploy a stateless stack
pnpm cdk-stateless deploy -e <stackname>
```

Examples:
```sh
# Deploy the toolchain pipeline stack (sets up CodePipeline in the bastion account)
pnpm cdk-stateless deploy -e OrcaBusStatelessSashRegressionStack

# Manually deploy the SashRegression stack to the beta (dev) environment
pnpm cdk-beta deploy SashRegressionStack

# Manually deploy to gamma
pnpm cdk-gamma deploy SashRegressionStack

# Manually deploy to prod
pnpm cdk-prod deploy SashRegressionStack
```

### Stacks

This CDK project manages multiple stacks. The root stack (the only one that does not include `DeploymentPipeline` in its stack ID) is deployed in the toolchain account and sets up a CodePipeline for cross-environment deployments to `beta` and `prod`.

To list all available stacks, run:

```sh
pnpm cdk-stateless ls
```


Development
--------------------------------------------------------------------------------

### Project Structure

The root of the project is an AWS CDK project where the main application logic lives inside the `./app` folder.

The project is organized into the following key directories:

- **`./app`**: Contains the main application logic — the `comparator`, `submitter`, and `watcher` Python packages and their Lambda handlers, plus shared tests in `./app/tests`. You can open the code editor directly in this folder, and the application should run independently.

- **`./bin/deploy.ts`**: Serves as the entry point of the application. It initializes two root stacks: `stateless` and `stateful`. You can remove one of these if your service does not require it.

- **`./infrastructure`**: Contains the infrastructure code for the project:
  - **`./infrastructure/toolchain`**: Includes stacks for the stateless and stateful resources deployed in the toolchain account. These stacks primarily set up the CodePipeline for cross-environment deployments.
  - **`./infrastructure/stage`**: Defines the stage stacks for different environments:
    - **`./infrastructure/stage/config.ts`**: Contains environment-specific configuration files (e.g., `beta`, `prod`).
    - **`./infrastructure/stage/constants.ts`**: Defines the testdata/results bucket names and S3 config paths.
    - **`./infrastructure/stage/deployment-stack.ts`**: The CDK stack entry point for provisioning the Comparator, Submitter, and Watcher functions, the Submitter API, the Watcher EventBridge rule, and a dedicated IAM role per function.

- **`.github/workflows/pr-tests.yml`**: Configures GitHub Actions to run tests for `make check` (linting and code style), tests defined in `./test`, and `make test` for the `./app` directory.

- **`./test`**: Contains tests for CDK code compliance against `cdk-nag`.

### Setup

#### Requirements

```sh
node --version
v22.9.0

# Update Corepack (if necessary, as per pnpm documentation)
npm install --global corepack@latest

# Enable Corepack to use pnpm
corepack enable pnpm
```

#### Install Dependencies

To install all required dependencies, run:

```sh
make install
```

### Conventions

### Linting & Formatting

Automated checks are enforced via pre-commit hooks, ensuring only checked code is committed. For details consult the `.pre-commit-config.yaml` file.

Manual, on-demand checking is also available via `make` targets. For details consult the `Makefile` in the root of the project.

To run linting and formatting checks on the root project, use:

```sh
make check
```

To also lint the app (Python), use `check-all` — this is what CI runs:

```sh
make check-all
```

To automatically fix issues with ESLint and Prettier, run:

```sh
make fix
```

### Testing

Unit tests are available for the Lambda handler and comparison logic. Test code is hosted alongside business logic in `./app/tests/`.

```sh
# Python unit tests (no Docker required)
cd app && make test

# CDK infrastructure tests (requires Docker Desktop to be running)
pnpm test
```

You can also run the comparator container directly against real S3 data:

```sh
make build
make invoke AWS_PROFILE=<profile> TESTDATA_CONFIG_S3_URI=s3://... RESULT_S3_PREFIX=s3://...
```

> **Note:** The CDK tests synthesize the Lambda image using Docker. If Docker is not running, `pnpm test` will fail with `Cannot connect to the Docker daemon`. Start Docker Desktop before running CDK tests locally.

### Invoking the comparator in dev

After deploying to dev (see [CDK Commands](#cdk-commands)), invoke the Lambda directly via the AWS CLI.

#### 1. Find the function name

CDK appends a hash to the logical resource name, so the name is not stable across deploys:

```sh
aws lambda list-functions --profile umccr-dev-pu --region ap-southeast-2 \
  --query 'Functions[?contains(FunctionName,`SashRegression`)].FunctionName' \
  --output text
```

#### 2. Check what config file the Lambda reads

The testdata config S3 URI is set as an environment variable at deploy time:

```sh
aws lambda get-function-configuration \
  --function-name <function-name> \
  --profile umccr-dev-pu \
  --query 'Environment.Variables.TESTDATA_CONFIG_S3_URI' \
  --output text
```

Fetch the config to see what cases are available:

```sh
aws s3 cp <TESTDATA_CONFIG_S3_URI> -
```

#### 3. Invoke

`case_name` must match the `metadata.case` field in the config YAML — not the tumor/normal library ID. Omit `case_name` to run all pairs.

```sh
aws lambda invoke \
  --function-name <function-name> \
  --payload '{"new_version":"0.7.0","baseline_version":"0.6.4","case_name":"SEQC-II-medium"}' \
  --cli-binary-format raw-in-base64-out \
  --profile umccr-dev-pu \
  /tmp/response.json && cat /tmp/response.json
```

Results are written to the `RESULT_S3_PREFIX` path shown in the Lambda configuration.


Glossary & References
--------------------------------------------------------------------------------

For general terms and expressions used across OrcaBus services, please see the platform [documentation](https://github.com/OrcaBus/wiki/blob/main/orcabus-platform/README.md#glossary--references).

Service specific terms:

| Term         | Description                                                                 |
|--------------|------------------------------------------------------------------------------|
| `sash`       | The UMCCR somatic/germline cancer reporting pipeline whose outputs are compared |
| Comparator   | The Lambda in this service that diffs new vs. baseline `sash` outputs       |
| Testdata bucket | Read-only S3 bucket holding curated baseline reference data and the test-case config YAML |
