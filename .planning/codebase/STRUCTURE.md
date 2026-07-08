# Codebase Structure

**Analysis Date:** 2026-07-01

## Directory Layout

```
service-sash-regression/
├── app/                          # Python Lambda application (Docker image root)
│   ├── comparator/               # Comparator library + Lambda handler
│   │   ├── lambdas/
│   │   │   └── comparator/
│   │   │       └── handler.py    # Lambda entry point (default CMD)
│   │   ├── comparison.py         # Subprocess wrapper for comprehensive script
│   │   ├── comprehensive_sash_comparison.py  # Deep VCF/TSV comparison script
│   │   ├── run_logging.py        # Tee logging utility for comparison script
│   │   ├── s3_utils.py           # S3 download/upload helpers
│   │   └── schema_check.py       # Validate 9 required sash output files
│   ├── submitter/                # Submitter library + Lambda handler
│   │   ├── lambdas/
│   │   │   └── submitter/
│   │   │       └── handler.py    # Lambda entry point (API Gateway or direct)
│   │   └── submit.py             # OrcaBus integration logic
│   ├── tests/                    # pytest test suite
│   │   ├── conftest.py
│   │   ├── test_comparator_handler.py
│   │   ├── test_comparison.py
│   │   ├── test_s3_utils.py
│   │   ├── test_schema_check.py
│   │   ├── test_submit.py
│   │   └── test_submitter_handler.py
│   ├── Dockerfile                # Lambda image (Python 3.12, bcftools from source)
│   ├── Makefile                  # install / test / check / fix / invoke-local
│   ├── requirements.txt          # Runtime deps (boto3, pandas, cyvcf2, pysam, requests, …)
│   └── requirements-dev.txt      # Dev/test deps (pytest, ruff, moto, …)
├── bin/
│   └── deploy.ts                 # CDK app entrypoint (deployMode context switch)
├── config/
│   └── testdata-cases.yaml       # Tumor/normal pair definitions (S3 paths for run1/run2)
├── infrastructure/
│   ├── stage/
│   │   ├── constants.ts          # Bucket names, SSM/secret names, stage-level constants
│   │   ├── config.ts             # getStackProps() per-stage factory
│   │   └── deployment-stack.ts   # SashRegressionStack (Lambda + API GW + IAM)
│   └── toolchain/
│       ├── stateless-stack.ts    # CodePipeline → beta/gamma/prod via DeploymentStackPipeline
│       └── stateful-stack.ts     # Stub — not yet implemented
├── scripts/
│   ├── run-comparator-local.py   # Local dev runner (mirrors Lambda without S3 I/O)
│   └── download-sash-446-new.sh  # Helper to download sash outputs for local testing
├── test/                         # CDK infrastructure tests (Jest/TypeScript)
│   ├── stage.test.ts
│   ├── toolchain.test.ts
│   └── utils.ts
├── work/                         # Local development data (gitignored)
│   ├── baseline/                 # Downloaded sash baseline outputs (by pair dir)
│   ├── new/                      # Downloaded sash new-version outputs
│   └── results/                  # Local comparison results
├── .github/workflows/
│   └── pr-tests.yml              # CI: lint+security, IaC tests, Python app tests
├── cdk.json                      # CDK context (app: bin/deploy.ts)
├── Makefile                      # Root-level orchestration (delegates to pnpm + app/Makefile)
├── package.json                  # Node.js deps (CDK, TypeScript, Jest, ESLint, Prettier)
├── pnpm-workspace.yaml           # pnpm workspace definition
└── tsconfig.json                 # TypeScript config for CDK code
```

## Directory Purposes

**`app/comparator/`:**

- Purpose: All code for the Comparator Lambda — from handler down to comparison utilities
- Contains: Lambda handler, schema checker, subprocess comparison wrapper, comprehensive comparison script, S3 utilities, logging helper
- Key files: `handler.py`, `schema_check.py`, `comparison.py`, `comprehensive_sash_comparison.py`, `s3_utils.py`

**`app/comparator/lambdas/comparator/`:**

- Purpose: Lambda-specific entry point module — the CMD target for the Docker image
- Contains: `handler.py` with `handler(event, context)` function only
- Key files: `handler.py`

**`app/submitter/`:**

- Purpose: All code for the Submitter Lambda — handler and OrcaBus integration
- Contains: Lambda handler and `submit.py` with full OrcaBus/WruDraftValidator logic
- Key files: `lambdas/submitter/handler.py`, `submit.py`

**`app/tests/`:**

- Purpose: pytest test suite covering both Lambda libraries
- Contains: Unit tests for all modules, shared conftest fixtures
- Key files: `conftest.py`, `test_comparator_handler.py`, `test_submitter_handler.py`

**`infrastructure/stage/`:**

- Purpose: CDK constructs defining the deployed service (Lambda functions, API Gateway, IAM)
- Contains: Deployment stack, constants, per-stage config factory
- Key files: `deployment-stack.ts`, `constants.ts`

**`infrastructure/toolchain/`:**

- Purpose: CDK constructs for the CI/CD pipeline (CodePipeline)
- Contains: Stateless (pipeline) stack and stateful stub
- Key files: `stateless-stack.ts`

**`config/`:**

- Purpose: Human-maintained testdata pair configuration — defines which tumor/normal pairs and S3 paths to use for regression comparisons
- Contains: `testdata-cases.yaml`
- Note: This file must be manually uploaded to S3 before running the Comparator Lambda

**`scripts/`:**

- Purpose: Developer utility scripts for local testing
- Contains: Local comparator runner, S3 download helper

**`work/`:**

- Purpose: Local working directory for downloaded sash outputs and comparison results during development
- Generated: No (populated manually via scripts)
- Committed: No (gitignored)

**`test/`:**

- Purpose: CDK infrastructure snapshot/unit tests (TypeScript/Jest)
- Contains: Tests verifying CDK stack synthesizes correctly

## Key File Locations

**Entry Points:**

- `app/comparator/lambdas/comparator/handler.py`: Comparator Lambda handler (`handler` function)
- `app/submitter/lambdas/submitter/handler.py`: Submitter Lambda handler (`handler` function)
- `bin/deploy.ts`: CDK app — select stack by `deployMode` context

**Configuration:**

- `infrastructure/stage/constants.ts`: Bucket names, secret/SSM names, EventBus name, default library IDs
- `infrastructure/stage/config.ts`: `getStackProps()` factory (add per-stage overrides here)
- `config/testdata-cases.yaml`: Testdata pair definitions (must be synced to S3)
- `cdk.json`: CDK app entrypoint declaration

**Core Logic:**

- `app/comparator/comparison.py`: Orchestrates subprocess call to `comprehensive_sash_comparison.py`
- `app/comparator/comprehensive_sash_comparison.py`: Deep VCF/TSV analysis and diff logic
- `app/comparator/schema_check.py`: Hardcoded list of 9 required sash output file templates
- `app/submitter/submit.py`: OrcaBus REST + WruDraftValidator + EventBridge integration

**Testing:**

- `app/tests/`: Python unit tests (pytest)
- `test/`: CDK TypeScript tests (Jest)
- `app/Makefile`: `make test` runs pytest with coverage

## Naming Conventions

**Files:**

- Python modules: `snake_case.py` (e.g., `schema_check.py`, `run_logging.py`)
- Python test files: `test_<module>.py` (e.g., `test_schema_check.py`)
- TypeScript CDK files: `kebab-case.ts` (e.g., `deployment-stack.ts`, `stateless-stack.ts`)
- Config files: `kebab-case.yaml` (e.g., `testdata-cases.yaml`)

**Directories:**

- Python package dirs: `snake_case/` (e.g., `comparator/`, `submitter/`)
- Lambda handler nesting: `lambdas/<lambda-name>/handler.py` (mirrors Lambda module path used in CMD)
- CDK tiers: `infrastructure/stage/` (per-stage resources) vs `infrastructure/toolchain/` (pipeline)

**CDK constructs:**

- Stack classes: `PascalCase` ending in `Stack` (e.g., `SashRegressionStack`, `StatelessStack`)
- Stack IDs (logical): `PascalCase` prefixed with `OrcaBus` for toolchain stacks (e.g., `OrcaBusStatelessSashRegressionStack`)

**Lambda module paths:**

- Comparator: `comparator.lambdas.comparator.handler.handler`
- Submitter: `submitter.lambdas.submitter.handler.handler`
- Pattern: `<package>.lambdas.<lambda-name>.handler.handler`

## Where to Add New Code

**New Lambda function:**

- Handler: `app/<service-name>/lambdas/<service-name>/handler.py`
- Business logic: `app/<service-name>/<module>.py`
- CDK definition: add `private create<Name>Function()` method in `infrastructure/stage/deployment-stack.ts`
- Override CMD in `DockerImageCode.fromImageAsset(..., {cmd: ['<service-name>.lambdas.<service-name>.handler.handler']})`

**New comparator utility module:**

- Implementation: `app/comparator/<module>.py`
- Tests: `app/tests/test_<module>.py`
- Import in handler via: `from comparator.<module> import <function>`

**New testdata pair:**

- Edit: `config/testdata-cases.yaml` — add entry to `pairs:` list with `tumor`, `normal`, `run1`, `run2`, `metadata` keys
- Upload: `aws s3 cp config/testdata-cases.yaml s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml`

**New CDK constant (per-stage):**

- Add field to return value of `getStageConstants()` in `infrastructure/stage/constants.ts` with a stage switch
- Reference in `deployment-stack.ts` via destructuring from `getStageConstants(props.stage)`

**Shared S3 helper:**

- Location: `app/comparator/s3_utils.py` — importable by both comparator and submitter packages

## Special Directories

**`work/`:**

- Purpose: Local development scratch space — holds downloaded sash outputs and comparison results
- Generated: Populated manually via `scripts/download-sash-446-new.sh` or `scripts/run-comparator-local.py`
- Committed: No (gitignored)

**`cdk.out/`:**

- Purpose: CDK synthesis output (CloudFormation templates, assets)
- Generated: Yes (`cdk synth`)
- Committed: No (gitignored)

**`app/.venv/`:**

- Purpose: Python virtual environment for `app/`
- Generated: Yes (`make install` in `app/`)
- Committed: No (gitignored)

**`.planning/codebase/`:**

- Purpose: Codebase analysis documents for GSD planning tools
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes

---

_Structure analysis: 2026-07-01_
