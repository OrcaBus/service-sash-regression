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

If you are not familiar with AWS, Lambda, or CDK, read the beginner guide first:

- [`docs/beginner-guide.md`](docs/beginner-guide.md)


Service Description
--------------------------------------------------------------------------------

### Name & responsibility

**Sash Regression** — compares `sash` pipeline outputs between a new version and a baseline version, to catch regressions before a release.

### Description

This service runs a Docker-based Lambda (the **Comparator**) that:

1. Downloads the new and baseline `sash` pipeline output directories from S3 (pipeline cache / project-data buckets) for a given test case.
2. Runs a schema check and a comprehensive comparison between the two output trees (`app/comparator/schema_check.py`, `app/comparator/comparison.py`, `app/comparator/comprehensive_sash_comparison.py`).
3. Uploads the comparison results to a results bucket and returns a pass/fail summary.

The set of test cases and their S3 locations is driven by a YAML config file (`testdata/config/sash-regression/testdata-cases.yaml`) read from the testdata bucket.

### API Endpoints

This service does not expose any API endpoints. The Lambda is invoked directly (manually, or by an external orchestrator) with a payload such as:

```json
{ "new_version": "0.7.0", "baseline_version": "0.6.4" }
```

### Consumed Events

This service does not consume any EventBridge events.

### Published Events

This service does not publish any EventBridge events.

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

### Permissions & Access Control

No end-user authentication or authorisation applies. The Lambda is invoked directly via the AWS API/console and is scoped via IAM to the specific S3 buckets listed above (`infrastructure/stage/deployment-stack.ts`).

### Change Management

#### Versioning strategy

Manual tagging of git commits following Semantic Versioning (semver) guidelines.

#### Release management

The service employs a fully automated CI/CD pipeline that automatically builds and releases all changes to the `main` branch across `beta` and `prod` environments.


Infrastructure & Deployment
--------------------------------------------------------------------------------

Infrastructure is managed via CDK. This template provides two types of CDK entry points: `cdk-stateless` and `cdk-stateful`.

### Stateful

This service has no stateful resources. The `StatefulStack` is kept as a placeholder.

### Stateless

- **`ComparatorFunction`** — Docker image Lambda (ARM64, 4096 MB, 10 GiB ephemeral storage, 15 min timeout) built from `./app`. Runs the schema check and comparison logic, reading `TESTDATA_CONFIG_S3_URI` and writing to `RESULT_S3_PREFIX`.

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
pnpm cdk-stateless deploy -e OrcaBusStatelessHelloWorldStack

# Manually deploy the SashRegression stack to the beta (dev) environment
pnpm cdk-stateless deploy SashRegressionStack -c deployMode=beta
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

- **`./app`**: Contains the main application logic — the `comparator` Python package and its Lambda handler. You can open the code editor directly in this folder, and the application should run independently.

- **`./bin/deploy.ts`**: Serves as the entry point of the application. It initializes two root stacks: `stateless` and `stateful`. You can remove one of these if your service does not require it.

- **`./infrastructure`**: Contains the infrastructure code for the project:
  - **`./infrastructure/toolchain`**: Includes stacks for the stateless and stateful resources deployed in the toolchain account. These stacks primarily set up the CodePipeline for cross-environment deployments.
  - **`./infrastructure/stage`**: Defines the stage stacks for different environments:
    - **`./infrastructure/stage/config.ts`**: Contains environment-specific configuration files (e.g., `beta`, `prod`).
    - **`./infrastructure/stage/constants.ts`**: Defines the testdata/results bucket names and S3 config paths.
    - **`./infrastructure/stage/deployment-stack.ts`**: The CDK stack entry point for provisioning the `ComparatorFunction` and its IAM role.

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
