# Design Document: Sash Regression Service — Completion

## Overview

`service-sash-regression` validates `sash` bioinformatics pipeline releases by running known test
cases against both the new version and a curated baseline, then comparing outputs
(VCFs, TSVs, Purple metrics, BCFtools stats) to detect regressions before a release reaches
production. Three Lambda functions are already deployed — Submitter, Watcher, Comparator — but
several critical gaps remain: no completion notification, no fan-out for multi-pair comparisons,
a monolithic comparison script with zero unit tests, stale per-stage constants, a token cache
without TTL, an S3 path-traversal vulnerability, and `cdk.out/` committed to git.

This document designs the **complete, production-ready state** of the service. It covers all
architectural changes, new components (Notifier Lambda, AggregatorFunction, comparison module
refactor), infrastructure changes (CDK constructs, IAM roles, env vars), and all hardening fixes.
The design is the target; the current code is the starting point.

---

## Architecture

### End-to-End Event Flow

```mermaid
sequenceDiagram
    participant Op as Operator
    participant API as API Gateway
    participant Sub as SubmitterFunction
    participant OB as OrcaBus (WM + MM)
    participant WRU as WruDraftValidator
    participant EB as EventBridge (OrcaBusMain)
    participant Watch as WatcherFunction
    participant Pair as PairComparatorFunction (×N)
    participant Agg as AggregatorFunction
    participant Notify as NotifierFunction
    participant S3 as S3 (umccr-research-dev)
    participant Slack as Slack

    Op->>API: POST /submit {tumor, normal, new_version, baseline_version}
    API->>Sub: invoke
    Sub->>OB: GET /api/v1/workflow (resolve codeVersion + pipelineId)
    Sub->>OB: GET /api/v1/workflowrun (check for existing umccr_tested_ run)
    alt existing SUCCEEDED or RUNNING
        Sub-->>API: {action: already_succeeded | already_running}
    else new run needed
        Sub->>OB: GET /api/v1/workflowrun (find prior SUCCEEDED sash run for inputs)
        Sub->>OB: GET /api/v1/payload (seed dragenSomaticDir / dragenGermlineDir)
        Sub->>WRU: invoke WruDraftValidator (DRAFT payload)
        Sub->>EB: PutEvents SashRegressionRunSubmitted
        Sub-->>API: {action: submitted, portal_run_id}
    end

    OB-->>EB: WorkflowRunStateChange SUCCEEDED (workflowRunName: umccr_tested_sash_...)
    EB-->>Watch: EventBridge rule triggers WatcherFunction
    Watch->>Watch: parse_run_name → new_version, baseline_version
    Watch->>Watch: extract new_output_path from event payload
    Watch->>S3: load testdata-cases.yaml config
    loop for each test-case pair
        Watch->>Pair: async invoke PairComparatorFunction {pair, new_output_path, ...}
    end

    Pair->>S3: download sash output dirs (baseline + new)
    Pair->>Pair: schema_check (9 required files)
    Pair->>Pair: run_comparison (refactored modules)
    Pair->>S3: upload pair results JSON
    Pair->>EB: PutEvents SashRegressionPairCompleted

    Agg-->>EB: SashRegressionPairCompleted (accumulates N results)
    Agg->>S3: check aggregate state; when all pairs done:
    Agg->>S3: upload rollup summary.json
    Agg->>EB: PutEvents SashRegressionComparisonCompleted

    EB-->>Notify: EventBridge rule triggers NotifierFunction
    Notify->>Slack: post message (PASS/FAIL verdict + S3 link)
```

### Component Map

```mermaid
graph TD
    subgraph Lambda Functions
        SUB[SubmitterFunction<br/>512 MB · 5 min · API GW]
        WATCH[WatcherFunction<br/>512 MB · 5 min · EventBridge]
        PAIR[PairComparatorFunction<br/>4096 MB · 15 min · 10 GiB /tmp]
        AGG[AggregatorFunction<br/>512 MB · 5 min · EventBridge]
        NOTIFY[NotifierFunction<br/>256 MB · 30 s · EventBridge]
    end

    subgraph IAM Roles (one per Lambda)
        SR[SubmitterRole]
        WR[WatcherRole]
        PR[PairComparatorRole]
        AR[AggregatorRole]
        NR[NotifierRole]
    end

    subgraph External
        APIGW[API Gateway REST]
        EB[OrcaBusMain EventBridge]
        OB[OrcaBus APIs]
        S3R[umccr-research-dev S3]
        S3T[test-data-* S3]
        WRU[WruDraftValidator Lambda]
        SECRET[SecretsManager JWT]
        SSM[SSM /hosted_zone/umccr/name]
        SLACK[Slack Incoming Webhook]
    end

    APIGW --> SUB
    SUB --> OB
    SUB --> WRU
    SUB --> EB
    EB --> WATCH
    WATCH --> S3R
    WATCH --> PAIR
    PAIR --> S3T
    PAIR --> S3R
    PAIR --> EB
    EB --> AGG
    AGG --> S3R
    AGG --> EB
    EB --> NOTIFY
    NOTIFY --> SLACK
    NOTIFY --> SECRET

    SUB --- SR
    WATCH --- WR
    PAIR --- PR
    AGG --- AR
    NOTIFY --- NR
```

---

## Data Models

### EventBridge Events

#### `SashRegressionRunSubmitted` (existing — no change)

```typescript
// source: "sash-regression.submitter"
// detailType: "SashRegressionRunSubmitted"
interface SashRegressionRunSubmittedDetail {
  portalRunId: string; // e.g. "202607085c1c01e1"
  newVersion: string; // e.g. "0.7.0"
  baselineVersion: string; // e.g. "0.6.4"
  workflowRunName: string; // umccr_tested_sash_0_7_0_vs_0_6_4_<portalRunId>
  tumorLibraryId: string;
  normalLibraryId: string;
}
```

#### `SashRegressionPairCompleted` (new)

```typescript
// source: "sash-regression.pair-comparator"
// detailType: "SashRegressionPairCompleted"
interface SashRegressionPairCompletedDetail {
  jobId: string; // UUID generated by Watcher per fan-out batch
  pairIndex: number; // 0-based index within the batch
  totalPairs: number; // total pairs in this batch
  newVersion: string;
  baselineVersion: string;
  caseName: string; // metadata.case value from config
  subject: string; // metadata.subject value
  tumorLibraryId: string;
  normalLibraryId: string;
  status: 'PASS' | 'WARN' | 'FAIL' | 'MANUAL_CHECK';
  criticalCount: number;
  criticalItems: string[];
  warningCount: number;
  warningItems: string[];
  metricsImpacted: boolean;
  schemaPassedBaseline: boolean;
  schemaPassedNew: boolean;
  resultsS3Prefix: string; // s3://umccr-research-dev/sash-regression/<new>-vs-<baseline>/<case>/<execId>/
}
```

#### `SashRegressionComparisonCompleted` (new)

```typescript
// source: "sash-regression.aggregator"
// detailType: "SashRegressionComparisonCompleted"
interface SashRegressionComparisonCompletedDetail {
  jobId: string;
  newVersion: string;
  baselineVersion: string;
  overallStatus: 'PASS' | 'WARN' | 'FAIL' | 'MANUAL_CHECK';
  totalPairs: number;
  passCount: number;
  warnCount: number;
  failCount: number;
  manualCheckCount: number;
  criticalCount: number;
  criticalItems: string[]; // up to 8 items across all pairs
  warningCount: number;
  warningItems: string[];
  metricsImpacted: boolean;
  allSchemaPassed: boolean;
  pairResults: SashRegressionPairCompletedDetail[];
  rollupS3Uri: string; // s3://umccr-research-dev/sash-regression/<new>-vs-<baseline>/rollup.json
  completedAt: string; // ISO-8601 UTC
}
```

### Aggregation State Object (written to S3)

The Aggregator reads and writes a JSON state blob at a deterministic S3 key so it can be invoked
once per `SashRegressionPairCompleted` event without needing DynamoDB.

```
s3://umccr-research-dev/sash-regression/<new>-vs-<baseline>/jobs/<jobId>/state.json
```

```typescript
interface AggregatorState {
  jobId: string;
  newVersion: string;
  baselineVersion: string;
  totalPairs: number;
  receivedPairs: number;
  pairs: SashRegressionPairCompletedDetail[];
  createdAt: string;
  updatedAt: string;
}
```

The Aggregator uses S3 conditional writes (`if-none-match` on create, object versioning + CAS on
update) to handle the rare case where two pair events arrive simultaneously. If the conditional
write fails (HTTP 412), the Lambda retries up to 3 times before raising. Given EventBridge's
at-least-once delivery and the short total pair count (≤10), retry on conflict is sufficient —
no distributed lock is needed.

### Testdata Config YAML

Structure is unchanged. The Watcher now reads it directly (previously the Comparator read it):

```yaml
alias_run1: 'sash 0.6.4'
alias_run2: 'sash 0.7.0'
pairs:
  - tumor: L2301218
    normal: L2301217
    run1: s3://umccr-research-dev/.../run1/L2301218__L2301217/
    run2: s3://umccr-research-dev/.../run2/L2301218__L2301217/
    metadata:
      subject: SBJ00480
      case: SEQC-II-medium
      cohort: SEQC-II
```

`run2` in config is the **baseline default** for the new version S3 path; the Watcher overrides it
with the live `new_output_path` extracted from the SUCCEEDED WorkflowRunStateChange event.

---

## Components and Interfaces

### 1. SubmitterFunction (hardening only — no new features)

No structural changes. Three targeted fixes:

**Token TTL fix** — `app/submitter/submit.py`:

```python
import time

_TOKEN_TTL_SECONDS = 600  # 10 min — well under typical OrcaBus JWT expiry

_token_cache: tuple[str, float] | None = None  # (token, fetched_at)

def _orcabus_token() -> str:
    global _token_cache
    now = time.monotonic()
    if _token_cache is None or (now - _token_cache[1]) > _TOKEN_TTL_SECONDS:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=ORCABUS_TOKEN_SECRET_ID)
        _token_cache = (json.loads(secret["SecretString"])["id_token"], now)
    return _token_cache[0]
```

**WRU validator error-body hardening** — `app/submitter/submit.py`:

```python
def _invoke_wru_validator(payload: dict) -> None:
    lam = boto3.client("lambda")
    response = lam.invoke(
        FunctionName=WRU_VALIDATOR_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    # Check Lambda invocation-level error (FunctionError header)
    if response.get("FunctionError"):
        raw = response["Payload"].read().decode()
        raise RuntimeError(f"WruDraftValidator Lambda error: {raw[:500]}")
    result = json.loads(response["Payload"].read())
    # WRU may return 200 HTTP status with an error body
    status_code = result.get("statusCode", 200)
    if status_code != 200:
        raise RuntimeError(f"WruDraftValidator returned statusCode={status_code}: {result}")
    body = result.get("body")
    if isinstance(body, str):
        body = json.loads(body)
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"WruDraftValidator error body: {body['error']}")
```

**`secrets.token_hex` for portal run ID** — `app/submitter/submit.py`:

```python
import secrets

def _create_portal_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d") + secrets.token_hex(4)
```

### 2. WatcherFunction (fan-out coordinator — extended)

The Watcher gains responsibility for loading the testdata config and fanning out one
`PairComparatorFunction` invocation per pair. A `jobId` (UUID) is generated per fan-out batch so
the Aggregator can correlate results.

**`app/watcher/track.py`** — updated interface:

```python
import uuid

def fan_out_comparisons(
    new_version: str,
    baseline_version: str,
    new_output_path: str,
    config: dict,
) -> str:
    """
    Invoke PairComparatorFunction once per pair in config["pairs"].
    Returns the jobId assigned to this fan-out batch.

    Preconditions:
      - config contains at least one entry in config["pairs"]
      - new_output_path is a valid s3:// URI
    Postconditions:
      - len(config["pairs"]) async Lambda invocations have been dispatched
      - Returns a UUID4 jobId consistent across all pair invocations
    """
    job_id = str(uuid.uuid4())
    pairs = config["pairs"]
    lam = boto3.client("lambda")
    for index, pair in enumerate(pairs):
        payload = {
            "jobId": job_id,
            "pairIndex": index,
            "totalPairs": len(pairs),
            "newVersion": new_version,
            "baselineVersion": baseline_version,
            "newOutputPath": new_output_path,
            "baselineOutputPath": pair["run1"],
            "tumor": pair["tumor"],
            "normal": pair["normal"],
            "metadata": pair.get("metadata", {}),
        }
        lam.invoke(
            FunctionName=PAIR_COMPARATOR_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )
    logger.info(f"Fan-out jobId={job_id} pairs={len(pairs)} new={new_version} baseline={baseline_version}")
    return job_id


def load_testdata_config(config_s3_uri: str) -> dict:
    """Load and return the testdata-cases.yaml config from S3."""
    bucket, key = parse_s3_uri(config_s3_uri)
    obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    return yaml.safe_load(obj["Body"].read())
```

**`app/watcher/lambdas/watcher/handler.py`** — updated handler:

```python
def handler(event: dict, context) -> None:
    detail = event["detail"]
    status = detail["status"]
    workflow_run_name = detail.get("workflowRunName", "")

    parsed = parse_run_name(workflow_run_name)
    if parsed is None:
        return

    new_version, baseline_version = parsed

    if status == "SUCCEEDED":
        payload_data = detail["payload"]["data"]
        output_uri = payload_data["engineParameters"]["outputUri"]
        sash_rel_path = payload_data["outputs"]["sashRelPath"]
        new_output_path = output_uri + sash_rel_path

        config = load_testdata_config(TESTDATA_CONFIG_S3_URI)
        fan_out_comparisons(new_version, baseline_version, new_output_path, config)
    elif status == "FAILED":
        logger.warning(f"Sash run FAILED: portalRunId={detail['portalRunId']}")
```

New environment variable required: `PAIR_COMPARATOR_FUNCTION_NAME`, `TESTDATA_CONFIG_S3_URI`.

### 3. PairComparatorFunction (renamed from ComparatorFunction)

The existing `ComparatorFunction` is renamed to `PairComparatorFunction`. It processes exactly
**one pair per invocation** — no sequential loop. The handler receives the pair payload directly
from the Watcher fan-out.

**`app/comparator/lambdas/pair_comparator/handler.py`** — new handler:

```python
EVENTS_BUS_NAME = os.environ["EVENTS_BUS_NAME"]
RESULT_S3_PREFIX = os.environ["RESULT_S3_PREFIX"]

def handler(event: dict, context) -> dict:
    job_id = event["jobId"]
    pair_index = event["pairIndex"]
    total_pairs = event["totalPairs"]
    new_version = event["newVersion"]
    baseline_version = event["baselineVersion"]
    new_output_path = event["newOutputPath"]
    baseline_output_path = event["baselineOutputPath"]
    tumor = event["tumor"]
    normal = event["normal"]
    metadata = event.get("metadata", {})

    with tempfile.TemporaryDirectory() as tmp:
        result = run_pair(
            tumor=tumor,
            normal=normal,
            metadata=metadata,
            baseline_s3=baseline_output_path,
            new_s3=new_output_path,
            tmp_path=Path(tmp),
            new_version=new_version,
            baseline_version=baseline_version,
            result_s3_prefix=RESULT_S3_PREFIX,
        )

    pair_event = build_pair_completed_event(
        job_id=job_id,
        pair_index=pair_index,
        total_pairs=total_pairs,
        new_version=new_version,
        baseline_version=baseline_version,
        tumor=tumor,
        normal=normal,
        metadata=metadata,
        result=result,
    )
    emit_event(pair_event, EVENTS_BUS_NAME)
    return pair_event
```

The `run_pair()` function is extracted from the existing handler into
`app/comparator/pair_runner.py` with the same logic but scoped to one pair only. Temp dir
lifecycle is now per-invocation (the Lambda itself is single-pair), so cleanup is automatic.

### 4. AggregatorFunction (new)

Triggered by `SashRegressionPairCompleted` events on `OrcaBusMain`. Accumulates pair results in
an S3 state blob. When `receivedPairs == totalPairs`, emits `SashRegressionComparisonCompleted`.

**`app/aggregator/lambdas/aggregator/handler.py`**:

```python
RESULTS_BUCKET = os.environ["RESULTS_BUCKET"]
RESULT_S3_PREFIX = os.environ["RESULT_S3_PREFIX"]
EVENTS_BUS_NAME = os.environ["EVENTS_BUS_NAME"]

def handler(event: dict, context) -> None:
    detail = event["detail"]
    job_id = detail["jobId"]
    total_pairs = detail["totalPairs"]

    state = load_or_create_state(job_id, total_pairs, RESULTS_BUCKET, RESULT_S3_PREFIX)
    state = append_pair(state, detail)
    save_state(state, RESULTS_BUCKET, RESULT_S3_PREFIX)

    if state["receivedPairs"] >= state["totalPairs"]:
        rollup = build_rollup(state)
        rollup_key = save_rollup(rollup, RESULTS_BUCKET, RESULT_S3_PREFIX)
        emit_completion_event(rollup, rollup_key, EVENTS_BUS_NAME)
        logger.info(f"All {total_pairs} pairs aggregated. Status={rollup['overallStatus']}")
```

**`app/aggregator/aggregate.py`**:

```python
def load_or_create_state(
    job_id: str, total_pairs: int, bucket: str, prefix: str
) -> dict:
    """
    Load existing aggregation state from S3, or create an empty state.

    Preconditions:
      - job_id is a valid UUID string
      - total_pairs >= 1
    Postconditions:
      - Returns a valid AggregatorState dict
      - If state does not exist in S3, returns a freshly initialised state
    """
    ...

def append_pair(state: dict, pair_detail: dict) -> dict:
    """
    Add one pair result to state. Idempotent on duplicate pairIndex.

    Preconditions:
      - pair_detail contains pairIndex within [0, state["totalPairs"])
    Postconditions:
      - state["pairs"] contains the pair_detail (no duplicates by pairIndex)
      - state["receivedPairs"] == len({p["pairIndex"] for p in state["pairs"]})
    """
    ...

def save_state(state: dict, bucket: str, prefix: str) -> None:
    """Write state to S3 with conditional put to prevent lost-update race."""
    ...

def build_rollup(state: dict) -> dict:
    """
    Aggregate pair statuses into a SashRegressionComparisonCompleted payload.

    Postconditions:
      - overallStatus is FAIL if any pair is FAIL
      - overallStatus is MANUAL_CHECK if any pair is MANUAL_CHECK (and no FAIL)
      - overallStatus is WARN if any pair is WARN (and no FAIL, no MANUAL_CHECK)
      - overallStatus is PASS only when all pairs are PASS
      - criticalItems truncated to 8 entries (same policy as existing compact summary)
    """
    ...
```

### 5. NotifierFunction (new)

Triggered by `SashRegressionComparisonCompleted` on `OrcaBusMain`. Posts a Slack message via
Incoming Webhook URL stored in Secrets Manager.

**`app/notifier/lambdas/notifier/handler.py`**:

```python
SLACK_WEBHOOK_SECRET_ID = os.environ["SLACK_WEBHOOK_SECRET_ID"]

def handler(event: dict, context) -> None:
    detail = event["detail"]
    webhook_url = get_slack_webhook_url(SLACK_WEBHOOK_SECRET_ID)
    message = format_slack_message(detail)
    post_slack_message(webhook_url, message)
```

**`app/notifier/notify.py`**:

```python
def format_slack_message(detail: dict) -> dict:
    """
    Build a Slack Block Kit message payload from a SashRegressionComparisonCompleted detail.

    Preconditions:
      - detail contains overallStatus, newVersion, baselineVersion, totalPairs,
        passCount, failCount, rollupS3Uri
    Postconditions:
      - Returns a valid Slack API payload dict with {"blocks": [...]}
      - Status emoji: ✅ PASS, ⚠️ WARN, ❌ FAIL, 🔍 MANUAL_CHECK
      - Includes S3 console link derived from rollupS3Uri
    """
    status = detail["overallStatus"]
    emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "MANUAL_CHECK": "🔍"}.get(status, "❓")
    new_ver = detail["newVersion"]
    base_ver = detail["baselineVersion"]
    total = detail["totalPairs"]
    pass_count = detail["passCount"]
    fail_count = detail["failCount"]
    rollup_uri = detail["rollupS3Uri"]
    s3_console = _s3_uri_to_console_url(rollup_uri)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Sash Regression: {new_ver} vs {base_ver}"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Status:* {status}  |  *Pairs:* {pass_count}/{total} passed  |  *Failures:* {fail_count}"}},
    ]
    if detail.get("criticalItems"):
        items_str = "\n• ".join(detail["criticalItems"][:5])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Critical issues:*\n• {items_str}"}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{s3_console}|View results in S3>"}})
    return {"blocks": blocks}


def get_slack_webhook_url(secret_id: str) -> str:
    """Retrieve Slack webhook URL from Secrets Manager. No caching (short-lived Lambda)."""
    sm = boto3.client("secretsmanager")
    secret = sm.get_secret_value(SecretId=secret_id)
    return json.loads(secret["SecretString"])["webhookUrl"]


def post_slack_message(webhook_url: str, payload: dict) -> None:
    """
    POST payload to Slack webhook.

    Postconditions:
      - Raises RuntimeError on non-2xx response
      - Does not raise if Slack returns 200 OK
    """
    resp = requests.post(webhook_url, json=payload, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")
```

### 6. Comparison Module Refactor

The 3,680-line `comprehensive_sash_comparison.py` is split into focused, independently testable
modules under `app/comparator/analysis/`. The subprocess boundary in `comparison.py` is
eliminated — the Comparator calls Python functions directly.

#### New Module Structure

```
app/comparator/
├── analysis/
│   ├── __init__.py
│   ├── runner.py          # run_pair_comparison() — orchestrates all analysis steps
│   ├── vcf_parser.py      # vcf_analysis(), count_vcf_variants() — no 10k cap
│   ├── tsv_parser.py      # parse_purple_purity(), parse_cnv_somatic(), parse_prioritised_sv()
│   ├── stats_parser.py    # parse_bcftools_stats()
│   ├── pcgr_parser.py     # parse_cancer_report_table(), parse_pcgr_msigs()
│   ├── reporter.py        # ComparisonReporter, build_compact_summary()
│   └── base_dir.py        # _get_base_dir() — sash 0.6.x vs 0.7.0 layout detection
├── comparison.py          # DEPRECATED shim (kept for backward compat, delegates to analysis.runner)
├── pair_runner.py         # run_pair() — extracted from old handler, calls analysis.runner
├── s3_utils.py            # (hardened — see below)
├── schema_check.py        # (unchanged)
└── lambdas/
    ├── comparator/        # DEPRECATED — old multi-pair handler (kept for direct-invoke SOP compat)
    │   └── handler.py
    └── pair_comparator/   # NEW — single-pair handler
        └── handler.py
```

#### Key Function Signatures

**`app/comparator/analysis/runner.py`**:

```python
def run_pair_comparison(
    run1: Path,
    run2: Path,
    tumor: str,
    normal: str,
    output_dir: Path,
) -> dict:
    """
    Run full comparison between two sash output directories.
    Replaces the subprocess call to comprehensive_sash_comparison.py.

    Preconditions:
      - run1 and run2 are directories that exist on disk
      - tumor and normal are non-empty strings
      - output_dir is writable
    Postconditions:
      - output_dir/summary.json is written with a valid compact summary dict
      - Returns the summary dict
      - Raises ComparisonError if a fatal analysis step fails
    """
```

**`app/comparator/analysis/vcf_parser.py`**:

```python
def vcf_analysis(vcf_path: Path, tumor: str) -> dict:
    """
    Analyse VCF variant counts, annotation fields, and examples.
    No record cap — processes all variants with bounded memory via streaming.

    Preconditions:
      - vcf_path exists and is a readable VCF/BCF (optionally gzipped)
    Postconditions:
      - Returns dict with keys: variant_count, pass_count, header_info, annotation_counts
      - Logs a WARNING if variant_count > 10_000 (informational, not truncated)
    """

def count_vcf_variants(vcf_path: Path) -> int:
    """Return total variant count using cyvcf2 streaming (no cap)."""
```

**`app/comparator/analysis/stats_parser.py`**:

```python
def parse_bcftools_stats(stats_path: Path) -> dict:
    """
    Parse BCFtools stats text output into a structured dict.

    Preconditions:
      - stats_path exists and is a readable text file
      - File contains BCFtools stats SN/TSTV/IDD sections
    Postconditions:
      - Returns dict with: snv_count, indel_count, ts_tv_ratio, insertion_counts, deletion_counts
      - Returns empty dict (not raises) if file is missing expected sections
    """
```

**`app/comparator/analysis/base_dir.py`**:

```python
def get_base_dir(run_dir: Path, tumor: str, normal: str) -> Path:
    """
    Resolve the sash output subdirectory for a given tumor/normal pair.
    Handles both pre-0.7.0 single-underscore layout and 0.7.0+ double-underscore layout.

    Preconditions:
      - run_dir exists on disk
      - tumor and normal are non-empty strings
    Postconditions:
      - Returns a Path that exists inside run_dir
      - Logs a WARNING if falling back to a constructed path that may not reflect actual layout
      - Raises FileNotFoundError if neither candidate path exists
    """
    single = run_dir / f"{tumor}_{normal}"
    double = run_dir / f"{tumor}__{normal}"
    if single.exists():
        return single
    if double.exists():
        return double
    # Log explicit warning before raising so the layout mismatch is observable in CloudWatch
    logger.warning(
        f"Neither {single} nor {double} exists — sash layout not recognised for "
        f"tumor={tumor} normal={normal} in {run_dir}"
    )
    raise FileNotFoundError(f"Cannot find sash output dir for {tumor}/{normal} in {run_dir}")
```

**`app/comparator/s3_utils.py`** — path traversal fix:

```python
from pathlib import Path

def download_s3_dir(s3_uri: str, local_dir: Path) -> None:
    bucket, prefix = parse_s3_uri(s3_uri)
    s3 = _s3_client()  # module-level cached client
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(prefix):].lstrip("/")
            dest = (local_dir / rel).resolve()
            # Use Path.is_relative_to() (Python 3.9+) — immune to string-prefix tricks
            if not dest.is_relative_to(local_dir.resolve()):
                raise ValueError(f"S3 key escapes local_dir: {key!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dest))
```

Module-level cached S3 client (eliminates per-call credential resolution):

```python
_S3_CLIENT = None

def _s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client("s3")
    return _S3_CLIENT
```

---

## CDK Infrastructure Changes

### New Lambda Constructs

`infrastructure/stage/deployment-stack.ts` adds two private factory methods:

```typescript
private createAggregatorFunction(resultS3Prefix: string, mainBus: IEventBus): IFunction
private createNotifierFunction(mainBus: IEventBus, slackWebhookSecretId: string): void
```

And modifies:

```typescript
private createWatcherFunction(
  pairComparatorFn: IFunction,
  mainBus: IEventBus,
  testdataConfigS3Uri: string,
): void

private createPairComparatorFunction(
  testdataConfigS3Uri: string,
  resultS3Prefix: string,
  aggregatorFn: IFunction,
  mainBus: IEventBus,
): IFunction
```

The old `createComparatorFunction` is replaced by `createPairComparatorFunction`. The previous
multi-pair `ComparatorFunction` CDK resource is removed (or kept with a `RemovalPolicy.DESTROY`
comment if a backward-compat direct-invoke path is needed during transition).

### IAM Role Changes

| Role                 | Lambda                   | New Permissions Added                                                                                             | Removed                                           |
| -------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `WatcherRole`        | WatcherFunction          | `lambda:InvokeFunction` on PairComparatorFunction; `s3:GetObject`/`s3:ListBucket` on results bucket (config read) | `lambda:InvokeFunction` on old ComparatorFunction |
| `PairComparatorRole` | PairComparatorFunction   | `events:PutEvents` on OrcaBusMain                                                                                 | (renamed from ComparatorRole)                     |
| `AggregatorRole`     | AggregatorFunction (new) | `s3:GetObject`/`s3:PutObject`/`s3:ListBucket` on results bucket; `events:PutEvents` on OrcaBusMain                | —                                                 |
| `NotifierRole`       | NotifierFunction (new)   | `secretsmanager:GetSecretValue` on Slack webhook secret                                                           | —                                                 |

### New Environment Variables

| Lambda                 | Variable                        | Value source                                |
| ---------------------- | ------------------------------- | ------------------------------------------- |
| WatcherFunction        | `PAIR_COMPARATOR_FUNCTION_NAME` | `pairComparatorFn.functionName`             |
| WatcherFunction        | `TESTDATA_CONFIG_S3_URI`        | `testdataConfigS3Uri` (already in config)   |
| PairComparatorFunction | `EVENTS_BUS_NAME`               | `EVENT_BUS_NAME` constant                   |
| AggregatorFunction     | `RESULTS_BUCKET`                | `RESULTS_BUCKET` constant                   |
| AggregatorFunction     | `RESULT_S3_PREFIX`              | `resultS3Prefix` from stage constants       |
| AggregatorFunction     | `EVENTS_BUS_NAME`               | `EVENT_BUS_NAME` constant                   |
| NotifierFunction       | `SLACK_WEBHOOK_SECRET_ID`       | `slackWebhookSecretId` from stage constants |

### New EventBridge Rules

```typescript
// In createAggregatorFunction():
new Rule(this, 'AggregatorRule', {
  eventBus: mainBus,
  eventPattern: {
    source: ['sash-regression.pair-comparator'],
    detailType: ['SashRegressionPairCompleted'],
  },
  targets: [new LambdaFunction(aggregatorFn)],
});

// In createNotifierFunction():
new Rule(this, 'NotifierRule', {
  eventBus: mainBus,
  eventPattern: {
    source: ['sash-regression.aggregator'],
    detailType: ['SashRegressionComparisonCompleted'],
  },
  targets: [new LambdaFunction(notifierFn)],
});
```

### Per-Stage Constants Updates

`infrastructure/stage/constants.ts` adds `slackWebhookSecretId` to the `getStageConstants`
return type and expands the `WRU_VALIDATOR_FUNCTION_NAME` map to use real per-stage values:

```typescript
const WRU_VALIDATOR_FUNCTION_NAME: Record<StageName, string> = {
  BETA: 'OrcaBusBeta-WruValidatorS-WruDraftValidatorCE0E33B-qPMdDh7awGuX', // pragma: allowlist secret
  GAMMA: 'OrcaBusGamma-WruValidatorS-WruDraftValidatorXXXXXX-XXXXXXXXXX', // TODO: confirm
  PROD: 'OrcaBusProd-WruValidatorS-WruDraftValidatorYYYYYY-YYYYYYYYYY', // TODO: confirm
};

const SLACK_WEBHOOK_SECRET_ID: Record<StageName, string> = {
  BETA: 'sash-regression/slack-webhook-beta',
  GAMMA: 'sash-regression/slack-webhook-gamma',
  PROD: 'sash-regression/slack-webhook',
};

export const getStageConstants = (stage: StageName) => ({
  testdataConfigS3Uri: `s3://${RESULTS_BUCKET}/${CONFIG_KEY}`,
  resultS3Prefix: `s3://${RESULTS_BUCKET}/${RESULT_KEY_PREFIX}`,
  wruDraftValidatorFunctionName: WRU_VALIDATOR_FUNCTION_NAME[stage],
  icaProjectId: ICA_PROJECT_ID[stage],
  pipelineCacheBucket: PIPELINE_CACHE_BUCKET[stage],
  slackWebhookSecretId: SLACK_WEBHOOK_SECRET_ID[stage],
});
```

### `.gitignore` Fix

Add to `.gitignore`:

```
# CDK synthesis artifacts — never commit these
cdk.out/
```

The five existing `cdk.out/asset.*` directories are removed from the repository via
`git rm -r --cached cdk.out/`.

### Docker CMD Update

The Dockerfile and CDK construct for `PairComparatorFunction` uses the new handler path:

```typescript
DockerImageCode.fromImageAsset(path.join(APP_ROOT), {
  cmd: ['comparator.lambdas.pair_comparator.handler.handler'],
});
```

AggregatorFunction and NotifierFunction override CMD similarly:

```typescript
cmd: ['aggregator.lambdas.aggregator.handler.handler'];
cmd: ['notifier.lambdas.notifier.handler.handler'];
```

---

## Updated Directory Structure

```
app/
├── aggregator/
│   ├── lambdas/aggregator/handler.py  # NEW — EventBridge trigger
│   └── aggregate.py                   # NEW — state management + rollup
├── comparator/
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── runner.py                  # NEW — replaces subprocess call
│   │   ├── vcf_parser.py              # EXTRACTED from comprehensive_sash_comparison.py
│   │   ├── tsv_parser.py              # EXTRACTED
│   │   ├── stats_parser.py            # EXTRACTED
│   │   ├── pcgr_parser.py             # EXTRACTED
│   │   ├── reporter.py                # EXTRACTED (ComparisonReporter + build_compact_summary)
│   │   └── base_dir.py                # EXTRACTED (_get_base_dir)
│   ├── lambdas/
│   │   ├── comparator/handler.py      # KEPT (direct-invoke compat, delegates to pair_runner)
│   │   └── pair_comparator/handler.py # NEW — single-pair handler
│   ├── pair_runner.py                 # NEW — extracted run_pair() logic
│   ├── comparison.py                  # KEPT as shim (delegates to analysis.runner)
│   ├── comprehensive_sash_comparison.py # RETAINED (still invocable as CLI)
│   ├── run_logging.py                 # HARDENED (detect Lambda env, skip atexit)
│   ├── s3_utils.py                    # HARDENED (is_relative_to, cached client)
│   └── schema_check.py                # UNCHANGED
├── notifier/
│   ├── lambdas/notifier/handler.py    # NEW — EventBridge trigger
│   └── notify.py                      # NEW — Slack formatting + delivery
├── submitter/
│   ├── lambdas/submitter/handler.py   # UNCHANGED
│   └── submit.py                      # HARDENED (token TTL, WRU body check, secrets.token_hex)
├── watcher/
│   ├── lambdas/watcher/handler.py     # EXTENDED (load config, fan_out_comparisons)
│   └── track.py                       # EXTENDED (fan_out_comparisons, load_testdata_config)
└── tests/
    ├── conftest.py
    ├── test_aggregate.py              # NEW
    ├── test_analysis_base_dir.py      # NEW
    ├── test_analysis_vcf_parser.py    # NEW
    ├── test_analysis_tsv_parser.py    # NEW
    ├── test_analysis_stats_parser.py  # NEW
    ├── test_analysis_pcgr_parser.py   # NEW
    ├── test_analysis_reporter.py      # NEW
    ├── test_comparator_handler.py     # EXTENDED
    ├── test_comparison.py             # EXTENDED
    ├── test_notify.py                 # NEW
    ├── test_pair_runner.py            # NEW
    ├── test_s3_utils.py               # EXTENDED (path traversal tests)
    ├── test_schema_check.py           # UNCHANGED
    ├── test_submit.py                 # EXTENDED (token TTL, WRU error body)
    ├── test_submitter_handler.py      # UNCHANGED
    └── test_watcher_handler.py        # EXTENDED (fan-out tests)
```

---

## Error Handling

### Pair Comparator Failures

If `PairComparatorFunction` raises an unhandled exception, Lambda retries it twice (async
invocation default). After exhausting retries, the invocation is discarded — the Aggregator will
never receive that pair's `SashRegressionPairCompleted` event and the job stalls silently.

**Mitigation**: Add a Lambda Dead Letter Queue (SQS) on `PairComparatorFunction`. A CloudWatch
alarm on the DLQ depth alerts the operator. The Aggregator state blob's `updatedAt` timestamp
can be used to detect stalled jobs (no update after N minutes = alarm).

Alternatively: emit a `SashRegressionPairCompleted` event with `status: "FAIL"` and
`criticalItems: ["lambda_invocation_error"]` from within the handler's top-level try/except,
guaranteeing the Aggregator always receives exactly `totalPairs` events.

The design mandates the **try/except emit** approach — simpler than DLQ, self-healing for the
aggregation:

```python
def handler(event: dict, context) -> dict:
    try:
        ...
        emit_event(pair_event, EVENTS_BUS_NAME)
        return pair_event
    except Exception as exc:
        logger.exception(f"Pair comparison failed: {exc}")
        error_event = build_pair_error_event(event, str(exc))
        emit_event(error_event, EVENTS_BUS_NAME)
        raise  # still fail the Lambda for CloudWatch visibility
```

### Aggregator Concurrent Write Conflict

Two `SashRegressionPairCompleted` events arriving within milliseconds will both try to update
the S3 state blob. Resolution:

1. Read state (with `VersionId` from GetObject response)
2. Append pair
3. Write state with `CopySourceIfMatch: VersionId` (conditional put — only succeeds if object
   hasn't changed since the read)
4. On `PreconditionFailed` (HTTP 412): sleep 200ms, retry up to 3 times
5. After 3 failures: raise — Lambda retries the event

This is safe: EventBridge delivers each event independently. Duplicate delivery of the same pair
event is handled by the `append_pair` idempotency check (dedup on `pairIndex`).

### Notifier Slack Failures

The Notifier does not retry failed Slack posts — the `SashRegressionComparisonCompleted` event
is delivered at-least-once by EventBridge. If the Slack call fails, Lambda's async retry (×2)
will re-invoke the Notifier. Slack Incoming Webhooks are idempotent enough for this purpose.
If the webhook secret is missing or invalid, the error surfaces in CloudWatch logs.

---

## Testing Strategy

### Unit Testing Approach

All AWS I/O is mocked with `unittest.mock.patch`. No moto usage required (existing pattern).

**New test files and their focus:**

| File                                 | What it tests                                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `test_analysis_vcf_parser.py`        | `vcf_analysis()` with a small synthetic VCF fixture; confirms no 10k cap; warns on >10k           |
| `test_analysis_stats_parser.py`      | `parse_bcftools_stats()` with a 5-line BCFtools SN/TSTV fixture snippet                           |
| `test_analysis_tsv_parser.py`        | `parse_purple_purity()`, `parse_cnv_somatic()` with minimal TSV fixtures                          |
| `test_analysis_pcgr_parser.py`       | `parse_cancer_report_table()` with mocked glob; confirms sorted `matches[0]` selection            |
| `test_analysis_base_dir.py`          | `get_base_dir()` with `tmp_path`: single-underscore found, double found, neither found            |
| `test_analysis_reporter.py`          | `build_compact_summary()` status rollup: all-pass, one-fail, mixed                                |
| `test_pair_runner.py`                | `run_pair()` mocking `download_s3_dir`, `check_schema`, `run_pair_comparison`, `upload_file`      |
| `test_aggregate.py`                  | `append_pair()` idempotency; `build_rollup()` status precedence; conditional write conflict retry |
| `test_notify.py`                     | `format_slack_message()` for each status emoji; `post_slack_message()` non-2xx raises             |
| `test_s3_utils.py` (extended)        | Path traversal attack: key `../../etc/passwd` raises ValueError via `is_relative_to()`            |
| `test_submit.py` (extended)          | Token TTL: second call within TTL skips SM; expired call re-fetches; WRU error body raises        |
| `test_watcher_handler.py` (extended) | Fan-out: N pairs → N Lambda invocations with correct `pairIndex`/`totalPairs`                     |

### Property-Based Testing

Not applicable — inputs are tightly constrained S3 URIs, fixed YAML configs, and structured
EventBridge event dicts. Fuzz testing the VCF/TSV parsers with arbitrary byte sequences is
out of scope (parsers are format-specific, not general).

### Integration Testing Approach

End-to-end integration is validated by the existing SOP workflow (PM.SR.2): submit a run via
the Submitter API, wait for `WorkflowRunStateChange SUCCEEDED`, confirm Comparator results
appear in S3 and Slack message is delivered. This is a manual gate-check, not automated CI.

CDK snapshot tests in `test/stage.test.ts` are extended to verify:

- Five Lambda functions exist (Submitter, Watcher, PairComparator, Aggregator, Notifier)
- Three EventBridge rules exist (Watcher rule, Aggregator rule, Notifier rule)
- `cdk-nag` passes with no suppressions added for new resources

---

## Security Considerations

| Concern                                     | Current state                            | Fix                                                                           |
| ------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------- |
| S3 path traversal in `download_s3_dir`      | String `startswith` prefix check         | Replace with `Path.is_relative_to()` (Python 3.9+)                            |
| Token cache no TTL                          | Module-level `_token_cache: str \| None` | Cache `(token, fetched_at)` tuple; re-fetch after 10 min                      |
| `random.choices` for portal run ID          | Mersenne Twister PRNG                    | `secrets.token_hex(4)`                                                        |
| WRU validator 200+error-body not caught     | Only checks `statusCode != 200`          | Inspect `body.error` field too                                                |
| Slack webhook URL in Secrets Manager        | Not yet implemented                      | Store at `sash-regression/slack-webhook-{stage}`                              |
| Broad wildcard S3 permissions on Comparator | `pipeline-*-cache-*` wildcard            | Documented acceptable risk; restrict to named buckets if known at deploy time |
| `cdk.out/` contains full app source copies  | Committed to git                         | Add to `.gitignore`, remove from repo                                         |

The `NotifierRole` grants `secretsmanager:GetSecretValue` only on the webhook secret ARN pattern
`sash-regression/slack-webhook*`, not a wildcard on all secrets.

---

## Performance Considerations

### Fan-Out Eliminates Timeout Risk

With fan-out, each `PairComparatorFunction` invocation processes exactly one pair. With 1 pair
currently and up to ~10 pairs in the future, the 15-minute timeout is ample for a single
5–10 GB download + comparison. The 10 GiB ephemeral storage is sufficient for one pair.

If a single pair's sash output exceeds 10 GiB (unlikely given current test cases), the fix is
to implement selective download — only fetch files referenced in `EXPECTED_FILES` and the
analysis paths used by the comparison modules. This is a follow-on optimisation, not in scope
for this completion work.

### S3 Client Caching

The module-level `_s3_client()` singleton in `s3_utils.py` eliminates per-call boto3 client
construction and credential resolution. For a large sash output directory (hundreds of objects),
this removes measurable overhead from the download loop.

### subprocess Elimination

Removing the subprocess boundary to `comprehensive_sash_comparison.py` eliminates:

- Python startup + module import time for pandas/boto3/cyvcf2 (~2–5 s per invocation)
- Filesystem IPC for `summary.json` / `metrics.json`
- Risk of partial output consumption on non-zero exit

The refactored `analysis.runner.run_pair_comparison()` is a direct function call with in-process
return value.

---

## Dependencies

No new runtime dependencies are required. All imports used by the new modules (`requests`,
`boto3`, `pyyaml`, `uuid`, `secrets`) are either in `requirements.txt` already or are Python
stdlib.

The Slack notification uses the Incoming Webhook API (POST to a webhook URL) — no Slack SDK
dependency is needed.

New Secrets Manager secrets (Slack webhook URLs per stage) must be created manually before
deploying the Notifier stack. A placeholder secret can be used for initial deployment; the
Notifier will simply fail to send (with a logged error) until the real URL is set.

---

## Correctness Properties

The following properties should hold across the complete implementation and can be verified via
unit tests or inspection:

1. **Fan-out completeness**: For every SUCCEEDED `WorkflowRunStateChange` event matching the
   `umccr_tested_` prefix, the number of `PairComparatorFunction` async invocations equals
   `len(config["pairs"])` with monotonically increasing `pairIndex` from 0 to `totalPairs-1`.

2. **Aggregation completeness**: When the Aggregator has received `totalPairs` distinct
   (by `pairIndex`) `SashRegressionPairCompleted` events for a given `jobId`, it emits exactly
   one `SashRegressionComparisonCompleted` event. For fewer than `totalPairs` events, it emits
   zero completion events.

3. **Status rollup monotonicity**: `overallStatus` is the worst status across all pairs.
   Formally: `FAIL` > `MANUAL_CHECK` > `WARN` > `PASS`. Any pair with `FAIL` forces the overall
   to `FAIL`, regardless of other pairs.

4. **Idempotent pair aggregation**: If the Aggregator receives the same `pairIndex` twice for the
   same `jobId` (duplicate event delivery), it counts the pair only once. The `receivedPairs`
   counter equals the cardinality of the set of distinct `pairIndex` values seen.

5. **Token refresh on expiry**: After `_TOKEN_TTL_SECONDS` have elapsed since the token was
   fetched, the next `_orcabus_token()` call fetches a new token from Secrets Manager rather
   than returning the cached value.

6. **WRU error propagation**: `_invoke_wru_validator()` raises `RuntimeError` for all three
   failure modes: Lambda `FunctionError`, HTTP `statusCode != 200`, and `body.error` non-null.
   No failure mode is silently swallowed.

7. **Path traversal prevention**: `download_s3_dir()` raises `ValueError` for any S3 object key
   that, when joined to the local download directory, resolves to a path outside that directory,
   regardless of `..` sequences, symlinks, or same-prefix-but-different-directory tricks.

8. **Schema check gate**: The pair comparator does not invoke the comparison modules if schema
   check fails for either the baseline or new run directory. The pair result has
   `status: "FAIL"`, `criticalItems: ["schema_check_failed"]`, and `comparison: null`.

9. **No sequential pair bottleneck**: The Watcher handler never calls the pair comparator in a
   loop within a single Lambda invocation. All pair invocations are fire-and-forget async
   Lambda calls dispatched in a loop then immediately returning.

10. **Slack message on every completion**: Every `SashRegressionComparisonCompleted` event
    triggers exactly one Slack message (subject to EventBridge at-least-once delivery; duplicate
    messages are acceptable, missed messages are not).
