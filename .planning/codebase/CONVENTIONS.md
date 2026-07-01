# Coding Conventions

**Analysis Date:** 2026-07-01

## Languages

This is a dual-language repo:

- **TypeScript** — CDK infrastructure (`infrastructure/`, `bin/`, `test/`)
- **Python** — Lambda application code (`app/comparator/`, `app/submitter/`, `app/tests/`)

Each language layer has its own conventions documented below.

---

## TypeScript Conventions

### Naming Patterns

**Files:**

- `kebab-case` for all `.ts` files: `deployment-stack.ts`, `stateless-stack.ts`, `stateful-stack.ts`

**Classes:**

- `PascalCase`: `SashRegressionStack`, `StatelessStack`, `StatefulStack`
- CDK construct IDs are also `PascalCase` strings: `'LambdaRole'`, `'ComparatorFunction'`, `'SubmitterApi'`

**Constants:**

- `SCREAMING_SNAKE_CASE` for module-level constants: `TESTDATA_BUCKET`, `RESULTS_BUCKET`, `EVENT_BUS_NAME`
- `camelCase` for destructured config values: `testdataConfigS3Uri`, `resultS3Prefix`, `wruDraftValidatorFunctionName`

**Interfaces:**

- `PascalCase` with suffix `Props` for CDK stack props: `SashRegressionStackProps`

**Functions:**

- `camelCase` for exported functions: `getStackProps`, `getStageConstants`
- Private class methods are `camelCase`: `createComparatorFunction`, `createSubmitterFunction`

### Code Style

**Formatter:** Prettier

- `printWidth`: 100
- `singleQuote`: true
- `semi`: true
- `tabWidth`: 2
- `trailingComma`: 'es5'
- `arrowParens`: 'always'
- `bracketSameLine`: true

**Linter:** ESLint (`eslint.config.js`) with `typescript-eslint` recommended rules.

- App folder (`app/*`) is explicitly excluded from ESLint scope — the Python app has its own tooling.

### Import Organization

**Order (observed pattern):**

1. Node builtins: `import * as path from 'path'`
2. Framework imports (`aws-cdk-lib`, `constructs`)
3. Named CDK service imports: `import { DockerImageCode, ... } from 'aws-cdk-lib/aws-lambda'`
4. Internal constants/config: `import { APP_ROOT, ... } from './constants'`
5. Third-party platform constructs: `import { StageName } from '@orcabus/platform-cdk-constructs/...'`

**No barrel files** — each module is imported directly by path.

### Private Class Members

Private CDK resources are declared as `private readonly` class members:

```typescript
export class SashRegressionStack extends Stack {
  private readonly lambdaRole: Role;
  // ...
}
```

Resource-creation logic is extracted into `private` methods (`createComparatorFunction`, `createSubmitterFunction`).

### Error Handling (TypeScript)

- Top-level `deploy.ts` uses explicit `throw new Error(...)` for invalid context values.
- No try/catch in CDK infrastructure code — errors propagate during synthesis.

### Comments

- Inline `//` comments explain _why_, not what: `// Read-only — never write here`
- Long comments before IAM policy blocks describe the intent of each permission group.
- `eslint-disable-next-line` used sparingly for intentional suppressions (`// eslint-disable-next-line @typescript-eslint/no-unused-vars`).
- `// pragma: allowlist secret` on literal strings that are not actual secrets but trigger scanners.

---

## Python Conventions

### Naming Patterns

**Files:**

- `snake_case` for all `.py` files: `s3_utils.py`, `schema_check.py`, `run_logging.py`, `comparison.py`
- Test files prefixed with `test_`: `test_comparator_handler.py`, `test_s3_utils.py`

**Functions:**

- Public: `snake_case` — `parse_s3_uri`, `download_s3_dir`, `check_schema`, `run_comparison`
- Private (module-internal): `_snake_case` — `_orcabus_token`, `_hostname`, `_auth_header`, `_create_portal_run_id`, `_find_existing_run`, `_build_draft_payload`

**Constants:**

- `SCREAMING_SNAKE_CASE` at module level: `TESTDATA_BUCKET`, `RESULTS_BUCKET`, `WORKFLOW_NAME`, `PAYLOAD_VERSION`
- `EXPECTED_FILES` in `schema_check.py` for the file list

**Type hints:**

- Used consistently on all public and private function signatures
- `str | None` union syntax (Python 3.10+ style) — not `Optional[str]`
- `Optional[str]` from `typing` used only in `run_logging.py` (older pattern)

### Code Style

**Formatter/Linter:** `ruff` (check + format)

- Run via `make check` (lint only) and `make fix` (lint + format)
- No `pyproject.toml` or `ruff.toml` found — ruff uses defaults
- Target: Python 3.13 (`.venv` uses 3.13)

**No docstrings on private helper functions** — they are self-describing.
**Module-level docstrings** on Lambda handler files describe the invocation contract.
**Function docstrings** on public API functions explain return shape: `check_schema`, `run_comparison`, `submit_sash_run`.

### Import Organization

**Order (observed pattern):**

1. Standard library: `import json`, `import logging`, `import os`, `from pathlib import Path`
2. Third-party: `import boto3`, `import requests`, `import yaml`
3. Internal: `from comparator.comparison import run_comparison`

**Absolute imports** used throughout — no relative imports.

### Module-Level Environment Reads

Environment variables are read at module import time for required vars:

```python
TESTDATA_CONFIG_S3_URI = os.environ["TESTDATA_CONFIG_S3_URI"]  # raises KeyError if missing
RESULT_S3_PREFIX = os.environ["RESULT_S3_PREFIX"]
```

Optional vars use `os.environ.get()` with defaults:

```python
TESTDATA_TUMOR_LIBRARY_ID = os.environ.get("TESTDATA_TUMOR_LIBRARY_ID", "L2301218")
```

### Error Handling (Python)

**Pattern:** raise built-in exceptions with descriptive messages.

- `ValueError` for bad input: `raise ValueError(f"No case '{case_name}' in testdata config")`
- `ValueError` for missing API resources: `raise ValueError(f"No workflow found: name=sash version={version}")`
- `RuntimeError` for external service failures: `raise RuntimeError(f"WruDraftValidator error: {result}")`
- `RuntimeError` for subprocess failures: `raise RuntimeError(f"comprehensive_sash_comparison.py failed: ...")`

No custom exception classes — rely on built-in types.

### Logging

**Framework:** `logging` (stdlib)

**Pattern:**

- Handler entry points use the root logger: `logger = logging.getLogger()` with `logger.setLevel(logging.INFO)`
- Supporting modules use `logger = logging.getLogger(__name__)`
- `logger.info(f"...")` for progress, `logger.warning(f"...")` for non-fatal issues, `logger.error(f"...")` before raising
- The final handler result is logged as a compact one-liner: `logger.info("FINAL_RESULT %s", json.dumps(...))`
- f-strings for most log messages; `%s` format used for the structured final log to avoid eager formatting

### Module Design

**Comparator package structure:**

- `comparator/s3_utils.py` — S3 I/O (download, upload, parse URI)
- `comparator/schema_check.py` — file presence validation
- `comparator/comparison.py` — subprocess wrapper for comparison script
- `comparator/run_logging.py` — stdout/stderr tee to log file
- `comparator/lambdas/comparator/handler.py` — Lambda entrypoint, orchestrates the above

**Submitter package structure:**

- `submitter/submit.py` — OrcaBus API calls, idempotency logic
- `submitter/lambdas/submitter/handler.py` — Lambda/API Gateway entrypoint

**Caching:** Module-level `None`-initialized caches for AWS calls within a Lambda warm start:

```python
_token_cache: str | None = None
_hostname_cache: str | None = None
```

### Path Handling

Use `pathlib.Path` throughout — not `os.path` string manipulation. `str(path)` conversion only at subprocess or boto3 call boundaries.

---

_Convention analysis: 2026-07-01_
