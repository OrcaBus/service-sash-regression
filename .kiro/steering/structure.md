# Project Structure

## Top-Level Layout

```
├── app/                        # Python Lambda application (Docker image root)
│   ├── comparator/             # Comparator library + Lambda handler
│   ├── submitter/              # Submitter library + Lambda handler
│   ├── watcher/                # Watcher library + Lambda handler
│   ├── tests/                  # pytest test suite (covers all three modules)
│   ├── Dockerfile              # Lambda image (Python 3.12, bcftools compiled from source)
│   ├── Makefile                # install / test / check / fix / invoke-local
│   ├── requirements.txt        # Runtime deps
│   └── requirements-dev.txt    # Dev/test deps
├── bin/deploy.ts               # CDK app entrypoint — deployMode context switch
├── config/
│   └── testdata-cases.yaml     # Tumor/normal pair definitions (S3 paths) — must be synced to S3
├── infrastructure/
│   ├── stage/                  # Per-environment application stacks
│   │   ├── constants.ts        # Bucket names, SSM/secret names, EventBus name, library IDs, WRU validator names
│   │   ├── config.ts           # getStackProps() per-stage factory
│   │   └── deployment-stack.ts # SashRegressionStack: 3 Lambdas, 3 IAM roles, API Gateway, EventBridge rule
│   └── toolchain/
│       ├── stateless-stack.ts  # CodePipeline → beta/gamma/prod
│       └── stateful-stack.ts   # Stub — not yet implemented
├── docs/
│   └── operation/
│       └── SOP/                # Standard Operating Procedures
│           ├── README.md       # SOP index
│           ├── PM.SR.1/        # Manual Comparator invocation
│           ├── PM.SR.2/        # Submitting a new sash version
│           ├── PM.SR.3/        # Service deployment
│           ├── PM.SR.4/        # Adding a testdata pair
│           ├── PM.SR.5/        # Troubleshooting
│           └── SR.1/           # generate-WRU-draft.sh CLI script + SOP doc
├── scripts/                    # Developer utility scripts for local testing
├── test/                       # CDK infrastructure tests (Jest/TypeScript)
├── work/                       # Local dev scratch space — gitignored
├── .kiro/steering/             # AI steering documents (this file)
├── cdk.json                    # CDK app config (entry: pnpx ts-node bin/deploy.ts)
├── Makefile                    # Root-level orchestration
└── package.json / pnpm-workspace.yaml
```

## `app/` — Application Logic

```
app/
├── comparator/
│   ├── lambdas/comparator/handler.py   # Lambda entry point (default Docker CMD)
│   ├── comparison.py                   # subprocess wrapper for comprehensive script
│   ├── comprehensive_sash_comparison.py # Deep VCF/TSV comparison (standalone script)
│   ├── run_logging.py                  # stdout/stderr tee to log file
│   ├── s3_utils.py                     # S3 download/upload helpers
│   └── schema_check.py                 # Validate 9 required sash output files
├── submitter/
│   ├── lambdas/submitter/handler.py    # Lambda entry point (API Gateway or direct)
│   └── submit.py                       # OrcaBus integration: lookup, validate, emit
├── watcher/
│   ├── lambdas/watcher/handler.py      # Lambda entry point (EventBridge rule)
│   └── track.py                        # parse_run_name() + invoke_comparator()
└── tests/
    ├── conftest.py
    ├── test_comparator_handler.py
    ├── test_comparison.py
    ├── test_s3_utils.py
    ├── test_schema_check.py
    ├── test_submit.py
    ├── test_submitter_handler.py
    └── test_watcher_handler.py
```

## Key Conventions

- **Event bus**: `OrcaBusMain`
- **Event source**: `sash-regression.submitter`
- **Event type emitted**: `SashRegressionRunSubmitted`
- **Results S3 prefix**: `sash-regression/<new>-vs-<baseline>/<case>/<exec_id>/test/`
- **Config S3 key**: `quentin/sash-regression/config/testdata-cases.yaml` on `umccr-research-dev`
- **workflowRunName pattern**: `umccr_tested_sash_{new_ver}_vs_{baseline_ver}_{portal_run_id}`
- **Lambda module paths**:
  - `comparator.lambdas.comparator.handler.handler` (default Docker CMD)
  - `submitter.lambdas.submitter.handler.handler`
  - `watcher.lambdas.watcher.handler.handler`
- All constants (bucket names, secret names, SSM paths, event names, WRU validator function names) live in `infrastructure/stage/constants.ts` — do not hardcode elsewhere
- `config/testdata-cases.yaml` must be manually uploaded to S3 after any change

## IAM Role Structure

Each Lambda has its own least-privilege execution role to avoid CloudFormation circular dependencies (a shared role referencing one function's ARN breaks synthesis when another function uses the same role):

| Role             | Lambda             | Grants                                                                                                             |
| ---------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `ComparatorRole` | ComparatorFunction | S3 read on pipeline-_-cache-_, project-data-\*, testdata bucket; S3 read/write on results bucket                   |
| `SubmitterRole`  | SubmitterFunction  | SecretsManager read (OrcaBus token), SSM read (hostname), Lambda invoke (WruDraftValidator), EventBridge PutEvents |
| `WatcherRole`    | WatcherFunction    | Lambda invoke (ComparatorFunction)                                                                                 |

## Where to Add New Code

**New comparator utility module:**

- Implementation: `app/comparator/<module>.py`
- Tests: `app/tests/test_<module>.py`
- Import in handler: `from comparator.<module> import <function>`

**New Lambda function:**

- Handler: `app/<service>/lambdas/<service>/handler.py`
- Business logic: `app/<service>/<module>.py`
- CDK: add `private create<Name>Function()` in `infrastructure/stage/deployment-stack.ts`
- Override CMD: `DockerImageCode.fromImageAsset(..., {cmd: ['<service>.lambdas.<service>.handler.handler']})`
- Add a new dedicated IAM role — do not share roles across functions

**New testdata pair:**

- Edit `config/testdata-cases.yaml` (add to `pairs:` with `tumor`, `normal`, `run1`, `run2`, `metadata`)
- Upload: `aws s3 cp config/testdata-cases.yaml s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml`

**New CDK constant (per-stage):**

- Add to `getStageConstants()` in `infrastructure/stage/constants.ts` with a stage switch
- Reference in `deployment-stack.ts` via destructuring

## SOPs

Operational procedures live in `docs/operation/SOP/` following the OrcaBus SOP convention.

Each SOP directory contains `PM.SR.<N>-<Title>.md` or `SR.<N>-<Title>.md` plus any supporting scripts. The `SR.1/` directory also contains `generate-WRU-draft.sh`, the CLI wrapper for the Submitter API.
