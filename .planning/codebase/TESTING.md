# Testing Patterns

**Analysis Date:** 2026-07-01

## Overview

Two separate test suites — one per language layer:

| Layer               | Framework      | Location     | Purpose                                  |
| ------------------- | -------------- | ------------ | ---------------------------------------- |
| TypeScript (CDK)    | Jest + ts-jest | `test/`      | CDK synthesis + cdk-nag compliance       |
| Python (Lambda app) | pytest         | `app/tests/` | Unit tests for all Lambda business logic |

---

## TypeScript Test Suite

### Test Framework

**Runner:** Jest 29

- Config: `jest.config.js`
- Transform: `ts-jest` for `.tsx?` files
- Test environment: `node`

**Run Commands:**

```bash
pnpm test              # tsc compile then jest
```

**Config (`jest.config.js`):**

```javascript
export const testEnvironment = 'node';
export const roots = ['<rootDir>/test'];
export const testMatch = ['**/*.test.ts'];
export const transform = { '^.+\\.tsx?$': 'ts-jest' };
```

### Test File Organization

**Location:** `test/` (separate from source, not co-located)

**Naming:** `<subject>.test.ts`

- `test/stage.test.ts` — tests for `SashRegressionStack`
- `test/toolchain.test.ts` — tests for `StatelessStack`
- `test/utils.ts` — shared helper (not a test file, no `test_` prefix)

### Test Structure

**Pattern:** `describe` block for one stack, `test` for each assertion:

```typescript
describe('cdk-nag-sash-regression-stack', () => {
  const app = new App({});
  const deployStack = new SashRegressionStack(app, 'SashRegressionStack', {
    ...getStackProps('BETA'),
    env: { account: '111111111111', region: 'ap-southeast-2' },
  });

  Aspects.of(deployStack).add(new AwsSolutionsChecks());
  applyNagSuppression(deployStack);

  test('cdk-nag AwsSolutions Pack errors', () => {
    const errors = Annotations.fromStack(deployStack)
      .findError('*', Match.stringLikeRegexp('AwsSolutions-.*'))
      .map(synthesisMessageToString);
    expect(errors).toHaveLength(0);
  });
});
```

**Stack setup** happens once at `describe` scope — not inside each `test`.

### Mocking (TypeScript)

No mocking used — these tests synthesize real CDK stacks against a dummy account ID `'111111111111'`. AWS calls never occur; only CloudFormation synthesis is tested.

### Shared Utilities

`test/utils.ts` exports a single helper used across test files:

```typescript
export function synthesisMessageToString(sm: SynthesisMessage): string {
  return `${sm.entry.data} [${sm.id}]`;
}
```

Previously duplicated in `stage.test.ts` — refactored into shared util when `toolchain.test.ts` was added.

### cdk-nag Suppressions

Suppressions are applied in helper functions defined after the `describe` block:

```typescript
function applyNagSuppression(stack: Stack) {
  NagSuppressions.addStackSuppressions(
    stack,
    [
      {
        id: 'AwsSolutions-L1',
        reason: 'Python 3.12 is the current platform standard runtime version',
      },
      // ...
    ],
    true
  );
}
```

Every suppression **must include a `reason`** string explaining the business justification. This is enforced by code review convention, not tooling.

---

## Python Test Suite

### Test Framework

**Runner:** pytest 7+

- Config: none (uses defaults)
- Coverage: `pytest-cov` (`--cov=comparator --cov=submitter --cov-report=term-missing`)
- AWS mocking: `moto[events]` 5+ (available but not used in current tests — `unittest.mock.patch` used instead)

**Run Commands:**

```bash
# From app/
make test              # pytest tests/ --cov=comparator --cov=submitter --cov-report=term-missing
make check             # ruff lint only
make fix               # ruff fix + format
```

### Test File Organization

**Location:** `app/tests/` (separate directory, not co-located with source)

**Naming:** `test_<module>.py` matching the source module name:

- `test_comparator_handler.py` → `comparator/lambdas/comparator/handler.py`
- `test_comparison.py` → `comparator/comparison.py`
- `test_s3_utils.py` → `comparator/s3_utils.py`
- `test_schema_check.py` → `comparator/schema_check.py`
- `test_submit.py` → `submitter/submit.py`
- `test_submitter_handler.py` → `submitter/lambdas/submitter/handler.py`

**`conftest.py`:** Present but empty — no shared fixtures defined at the suite level.

### Test Structure

**Classes for grouping:** Tests that belong to the same unit are grouped in a `class Test<Subject>`:

```python
class TestHandler:
    def test_emits_compact_summary_and_final_log_line(self): ...
    def test_warn_status_when_metric_delta_below_threshold(self): ...
    def test_fail_status_when_key_files_changed(self): ...
```

**Top-level functions** used for simpler modules where a class would be overhead (`test_submit.py`, `test_submitter_handler.py`).

**Test name convention:** `test_<what it does>` — behavior-describing, not implementation-describing. Names read as assertions: `test_skips_comparison_when_schema_fails`, `test_raises_on_unknown_case_name`.

### Mocking

**Framework:** `unittest.mock` (`patch`, `MagicMock`) — no pytest-mock.

**Primary pattern — context manager `with patch(...):`**

```python
with (
    patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
    patch("comparator.lambdas.comparator.handler.download_s3_dir"),
    patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_PASS),
    patch("comparator.lambdas.comparator.handler.run_comparison", return_value=cmp_summary),
    patch("comparator.lambdas.comparator.handler.upload_file"),
):
    result = handler({"new_version": "0.7.0", "baseline_version": "0.6.4"}, None)
```

**Python 3.10+ parenthesized `with` blocks** used for multiple simultaneous patches — avoids nested `with` or `@patch` decorator stacking.

**Patch target:** Always patch at the **import site** in the module under test (e.g., `comparator.lambdas.comparator.handler.load_config`), not the definition site.

**boto3 fixture pattern** (`test_submit.py`):

```python
@pytest.fixture()
def mock_boto3():
    with patch("submitter.submit.boto3") as m:
        sm = MagicMock()
        sm.get_secret_value.return_value = {"SecretString": json.dumps({"id_token": "fake-token"})}
        m.client.side_effect = lambda svc, **kw: {"secretsmanager": sm, "ssm": ssm, ...}[svc]
        yield {"sm": sm, "ssm": ssm, "lambda": lam, "events": events}
```

**What is mocked:**

- All AWS SDK calls (`boto3.client`, individual service methods)
- HTTP requests (`requests.get`)
- All S3 I/O (`download_s3_dir`, `upload_file`, `load_config`)
- Subprocess calls (`subprocess.run`)

**What is NOT mocked:**

- Pure Python logic (string manipulation, dict building, path operations)
- `pathlib.Path` usage — `tmp_path` fixture used instead for filesystem tests

### Fixtures

**`tmp_path`** (built-in pytest fixture) used for all filesystem tests — no custom tmp dir setup.

**`autouse=True` fixtures** for environment setup:

```python
@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    import submitter.submit as s
    s._token_cache = None   # reset module-level cache between tests
    s._hostname_cache = None
```

**Module-level constants** used as shared test data instead of fixtures:

```python
_CONFIG = {"pairs": [...]}
_SCHEMA_PASS = {"passed": True, "missing": [], "present": ["file.txt"]}
_SCHEMA_FAIL = {"passed": False, "missing": ["file.txt"], "present": []}
```

**Factory helpers** for complex mock objects:

```python
def _make_mock_result(returncode: int, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stderr = stderr
    return m
```

### Environment Variable Handling in Tests

Modules that read env vars at import time require the env to be set **before** import:

```python
# Must be set before the handler module is imported (module-level os.environ access)
os.environ.setdefault("TESTDATA_CONFIG_S3_URI", "s3://bucket/config.yaml")
os.environ.setdefault("RESULT_S3_PREFIX", "s3://bucket/results/")

from comparator.lambdas.comparator.handler import handler  # noqa: E402
```

The `# noqa: E402` comment suppresses the "module import not at top" lint warning — this is the accepted pattern for modules with side-effectful imports.

For modules that use `os.environ.get()` with defaults, `monkeypatch.setenv` in a fixture is sufficient.

### Error Path Testing

```python
def test_raises_on_unknown_case_name(self):
    with (
        patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
    ):
        with pytest.raises(ValueError, match="nonexistent"):
            handler({"new_version": "0.7.0", "baseline_version": "0.6.4", "case_name": "nonexistent"}, None)
```

Pattern: `pytest.raises(<ExceptionType>, match="<substring>")` — the `match` parameter validates the error message.

### Coverage

**Requirements:** No enforced minimum — coverage is reported but not gated.

**Report:**

```bash
make test   # shows term-missing report: which lines are uncovered
```

**Scope:** `--cov=comparator --cov=submitter` — only application code, not `tests/` itself.
The large `comprehensive_sash_comparison.py` (146KB) is within the `comparator` package but is a standalone script — coverage of that file is partial.

### Test Types

**Unit tests only** — all external I/O (S3, SSM, Lambda, EventBridge, HTTP) is mocked.

**No integration tests** — `make invoke-local` in `app/Makefile` provides a manual end-to-end path against real S3 (requires `AWS_PROFILE=umccr-dev-pu`).

**No E2E tests** — not applicable for this service.

**CDK synthesis tests** (TypeScript `test/`) are a form of snapshot/compliance test — they assert cdk-nag rules pass, not functional behavior.

---

_Testing analysis: 2026-07-01_
