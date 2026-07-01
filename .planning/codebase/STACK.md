# Technology Stack

**Analysis Date:** 2026-07-01

## Languages

**Primary:**

- Python 3.12 — Lambda application code (comparator and submitter functions). Specified in `app/Dockerfile` as the Lambda base image. Dev environment runs Python 3.13 but Lambda runtime is pinned to 3.12.
- TypeScript 5.8 — AWS CDK infrastructure-as-code (`infrastructure/`, `bin/`, `test/`)

## Runtime

**Environment:**

- Node.js 24.x — CDK synthesis and IaC tests (specified in `.github/workflows/pr-tests.yml`)
- AWS Lambda ARM64 — Python functions deployed as Docker image lambdas

**Package Manager:**

- pnpm 10.30.2 (pinned via `packageManager` field in `package.json`)
- Lockfile: `pnpm-lock.yaml` (present, frozen during CI)
- Python: `pip` with `requirements.txt` / `requirements-dev.txt` in `app/`

## Frameworks

**Core:**

- AWS CDK 2.260.0 — Infrastructure provisioning (`aws-cdk-lib`, `constructs`)
- `@orcabus/platform-cdk-constructs` 1.2.6 — UMCCR OrcaBus platform constructs (shared accounts, event bus, deployment pipeline pattern)
- `@aws-cdk/aws-lambda-python-alpha` 2.260.0-alpha.0 — Python Lambda packaging helper

**Testing (IaC):**

- Jest 29.7 + ts-jest 29.3 — TypeScript CDK unit tests (`test/`)
- Config: `jest.config.js`

**Testing (App):**

- pytest 7.x + pytest-cov 4.x — Python unit tests (`app/tests/`)
- moto[events] 5.x — AWS service mocking (EventBridge events)

**Build/Dev:**

- ts-node 10.9 — CDK app entry point execution (`cdk.json` app: `pnpx ts-node bin/deploy.ts`)
- pre-commit — git hook runner (ESLint, Prettier, detect-secrets, TruffleHog)
- Docker — Lambda image build (`app/Dockerfile`)
- bcftools 1.21 — bioinformatics CLI tool compiled into Docker image from source

**Linting/Formatting:**

- ESLint 10 + typescript-eslint 8.57 — TypeScript linting (`eslint.config.mjs`)
- Prettier 3.5 — TypeScript/JSON formatting (`.prettierrc.json`)
- Ruff 0.4+ — Python linting and formatting (`app/`)

## Key Dependencies

**Critical:**

- `boto3` >=1.34 — AWS SDK for Python; used in comparator (S3) and submitter (Secrets Manager, SSM, Lambda, EventBridge)
- `pysam` >=0.22 — SAM/BAM/CRAM file handling for genomics comparison
- `cyvcf2` >=0.31 — VCF file parsing for variant comparison
- `pandas` >=2.0 — Tabular data comparison and analysis
- `pyyaml` >=6.0 — Testdata config parsing (`config/testdata-cases.yaml`)
- `requests` >=2.31 — HTTP calls to OrcaBus REST APIs (workflow and metadata managers)

**Infrastructure:**

- `@orcabus/platform-cdk-constructs` 1.2.6 — Provides `DeploymentStackPipeline`, shared account IDs, region, EventBridge bus name, Secrets Manager secret names, SSM parameter names
- `cdk-nag` 2.35 — CDK security and compliance checks

## Configuration

**Environment (Lambda runtime):**

- `TESTDATA_CONFIG_S3_URI` — S3 URI to YAML config listing comparison pairs (comparator)
- `RESULT_S3_PREFIX` — S3 prefix where comparison results are written (comparator)
- `ORCABUS_TOKEN_SECRET_ID` — Secrets Manager secret name for OrcaBus JWT (submitter)
- `HOSTNAME_SSM_PARAMETER_NAME` — SSM path for OrcaBus API hostname: `/hosted_zone/umccr/name` (submitter)
- `WRU_VALIDATOR_LAMBDA_NAME` — Name of the WruDraftValidator Lambda to invoke (submitter)
- `EVENTS_BUS_NAME` — EventBridge bus name: `OrcaBusMain` (submitter)
- `TESTDATA_TUMOR_LIBRARY_ID` / `TESTDATA_NORMAL_LIBRARY_ID` — Default library IDs for testdata runs

**Build:**

- `cdk.json` — CDK app entrypoint and watch config; deployMode selected via `-c deployMode=<mode>`
- `tsconfig.json` — TypeScript strict mode, ES2020 target, CommonJS modules
- `pnpm-workspace.yaml` — Dependency version overrides for security patches

## Platform Requirements

**Development:**

- Node.js 24.x, pnpm 10.30.2, Python 3.12+, Docker, AWS CLI with appropriate profile

**Production:**

- AWS Lambda ARM64 (Docker image runtime), region `ap-southeast-2`
- Deployed via AWS CodePipeline through `DeploymentStackPipeline` CDK construct
- Stages: BETA → GAMMA → PROD (accounts defined in `@orcabus/platform-cdk-constructs`)

---

_Stack analysis: 2026-07-01_
