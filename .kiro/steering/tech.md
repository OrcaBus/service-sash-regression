# Tech Stack

## Languages

- **TypeScript** — CDK infrastructure code (`infrastructure/`, `bin/`, `test/`). ES2020, strict mode, CommonJS modules.
- **Python 3.12** — Lambda application code (`app/`). Dev environment uses 3.13 but Lambda runtime is pinned to 3.12.

## Infrastructure

- **AWS CDK v2** (`aws-cdk-lib ^2.260.0`) with TypeScript
- **`@orcabus/platform-cdk-constructs` 1.2.6** — internal shared constructs (accounts, event bus, CodePipeline pattern)
- **`@aws-cdk/aws-lambda-python-alpha`** — Python Lambda packaging helper
- **cdk-nag** — CDK security/compliance checks in tests
- Both Lambdas share a **single Docker image** built from `app/`. Submitter overrides `CMD` at deploy time.

## Key AWS Services Used

- **AWS Lambda** ARM64, Docker image runtime
  - Comparator: 4096 MB, 10 GiB ephemeral, 15 min timeout
  - Submitter: 512 MB, 5 min timeout, fronted by API Gateway
- **Amazon S3** — source sash outputs (read), testdata config (read), comparison results (write)
- **Amazon EventBridge** — bus `OrcaBusMain`, event `SashRegressionRunSubmitted`
- **AWS Secrets Manager** — OrcaBus JWT token (`orcabus/token-service-jwt`)
- **AWS SSM Parameter Store** — OrcaBus hostname (`/hosted_zone/umccr/name`)
- **AWS API Gateway** — REST proxy in front of Submitter Lambda

## Package Manager

**pnpm** (10.30.2). Always use `pnpm`, never `npm` or `yarn`.

```sh
corepack enable pnpm
```

## Node Version

Node.js 24.x (CI). Local requirement: Node 22.9.0+.

## Build / Test / Lint Commands

```sh
# Install all dependencies (pnpm + Python venv)
make install

# Lint and format checks (TypeScript)
make check

# Lint + format check everything including Python app
make check-all

# Auto-fix lint and format issues
make fix

# Run CDK infrastructure tests (requires Docker)
pnpm test

# Run Python unit tests with coverage
cd app && make test

# Build and invoke the comparator container locally
make build
make invoke AWS_PROFILE=<profile> TESTDATA_CONFIG_S3_URI=s3://... RESULT_S3_PREFIX=s3://...

# CDK deploy commands
pnpm cdk-stateless ls
pnpm cdk-stateless deploy -e OrcaBusStatelessSashRegressionStack
pnpm cdk-beta deploy SashRegressionStack
pnpm cdk-gamma deploy SashRegressionStack
pnpm cdk-prod deploy SashRegressionStack
```

## Linting & Formatting

- **ESLint** (`eslint.config.mjs`) with `typescript-eslint` — TypeScript only (`app/` excluded)
- **Prettier** (`.prettierrc.json`) — `singleQuote: true`, `printWidth: 100`, `tabWidth: 2`
- **Ruff** — Python linting and formatting in `app/`
- **pre-commit** hooks: ESLint, Prettier, detect-secrets, TruffleHog

## Testing

- **Jest + ts-jest** for CDK infrastructure tests (`test/`) — validates cdk-nag compliance, not functional behaviour
- **pytest + pytest-cov** for Python unit tests (`app/tests/`) — all AWS I/O mocked via `unittest.mock`
- **moto[events]** available but `unittest.mock.patch` is the primary mocking pattern
- CDK tests require Docker Desktop running locally

## TypeScript Config Highlights

- `strict: true`, `noImplicitAny: true`, `strictNullChecks: true`
- `target: ES2020`, `module: commonjs`
- `resolveJsonModule: true`
