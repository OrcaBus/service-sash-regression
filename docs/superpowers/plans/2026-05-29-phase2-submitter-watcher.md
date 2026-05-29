# Phase 2 — Submitter + Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Submitter and Watcher Lambdas to `service-sash-regression` so a sash regression run can be submitted via HTTP POST and automatically invokes the Comparator when the run completes in OrcaBus.

**Architecture:** Submitter Lambda accepts `{new_version, baseline_output_path}` via API Gateway, submits a sash run to OrcaBus, and records the `portal_run_id` in DynamoDB. Watcher Lambda triggers on every sash `WorkflowRunStateChange` SUCCEEDED/FAILED event, ignores untracked runs via a DynamoDB lookup, and invokes the Comparator asynchronously on success. Comparator Lambda (Phase 1) is extended to accept optional path overrides so the Watcher can pass the live run output path directly.

**Tech Stack:** Python 3.13 ZIP Lambdas (boto3 runtime), DynamoDB, EventBridge, HTTP API Gateway, AWS CDK TypeScript, moto for tests, pytest.

---

## File map

```
app/
  comparator/lambdas/comparator/handler.py  ← MODIFY: add new_output_path / baseline_output_path overrides
  submitter/
    __init__.py                              ← CREATE (empty)
    db.py                                    ← CREATE: put_run (stores baseline_version too), get_run, update_run_status
    submit.py                                ← CREATE: OrcaBus submission stub (TODO with Florian)
    lambdas/
      __init__.py                            ← CREATE (empty)
      submitter/
        __init__.py                          ← CREATE (empty)
        handler.py                           ← CREATE: API Gateway handler
  watcher/
    __init__.py                              ← CREATE (empty)
    db.py                                    ← CREATE: identical to submitter/db.py
    track.py                                 ← CREATE: DynamoDB lookup + Comparator invocation
    lambdas/
      __init__.py                            ← CREATE (empty)
      watcher/
        __init__.py                          ← CREATE (empty)
        handler.py                           ← CREATE: EventBridge handler
  tests/
    test_comparator_handler.py               ← MODIFY: add tests for path override params
    test_submitter_handler.py                ← CREATE
    test_watcher_handler.py                  ← CREATE
    test_db.py                               ← CREATE
infrastructure/stage/
  deployment-stack.ts                        ← MODIFY: add DynamoDB, Submitter, Watcher, EventBridge rule, HTTP API
  constants.ts                               ← MODIFY: add REGRESSION_TABLE_NAME, EVENT_BUS_NAME
```

---

## Task 1: Extend Comparator handler with path overrides

**Files:**

- Modify: `app/comparator/lambdas/comparator/handler.py`
- Modify: `app/tests/test_comparator_handler.py`

- [ ] **Step 1: Write the failing test**

Add to `app/tests/test_comparator_handler.py`:

```python
def test_new_output_path_overrides_config_run2(self):
    """Watcher can pass new_output_path directly, bypassing config run2."""
    captured = []
    with (
        patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
        patch("comparator.lambdas.comparator.handler.download_s3_dir"),
        patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_PASS),
        patch("comparator.lambdas.comparator.handler.run_comparison", side_effect=_make_run_comparison(captured)),
        patch("comparator.lambdas.comparator.handler.upload_file"),
    ):
        result = handler(
            {
                "new_version": "0.7.0",
                "baseline_version": "0.6.4",
                "new_output_path": "s3://override/new/",
                "baseline_output_path": "s3://override/baseline/",
            },
            None,
        )
    assert result["all_schema_passed"] is True

def test_baseline_output_path_overrides_config_run1(self):
    captured = []
    with (
        patch("comparator.lambdas.comparator.handler.load_config", return_value=_CONFIG),
        patch("comparator.lambdas.comparator.handler.download_s3_dir") as mock_dl,
        patch("comparator.lambdas.comparator.handler.check_schema", return_value=_SCHEMA_PASS),
        patch("comparator.lambdas.comparator.handler.run_comparison", side_effect=_make_run_comparison(captured)),
        patch("comparator.lambdas.comparator.handler.upload_file"),
    ):
        handler(
            {
                "new_version": "0.7.0",
                "baseline_version": "0.6.4",
                "baseline_output_path": "s3://my-override/baseline/",
            },
            None,
        )
    # First download_s3_dir call should use the override path
    first_call_uri = mock_dl.call_args_list[0][0][0]
    assert first_call_uri == "s3://my-override/baseline/"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd app && pytest tests/test_comparator_handler.py -v -k "override"
```

Expected: FAIL — `handler` doesn't accept `new_output_path` yet.

- [ ] **Step 3: Update Comparator handler**

In `app/comparator/lambdas/comparator/handler.py`, update `handler()`:

```python
def handler(event: dict, context) -> dict:
    new_version = event["new_version"]
    baseline_version = event["baseline_version"]
    case_name = event.get("case_name")
    new_output_path = event.get("new_output_path")        # optional: from Watcher
    baseline_output_path = event.get("baseline_output_path")  # optional: from Watcher

    logger.info(f"Comparing sash {new_version} vs {baseline_version}")

    config = load_config(TESTDATA_CONFIG_S3_URI)
    pairs = config["pairs"]

    if case_name:
        pairs = [p for p in pairs if p["metadata"].get("case") == case_name]
        if not pairs:
            raise ValueError(f"No case '{case_name}' in testdata config")

    if new_output_path:
        for p in pairs:
            p["run2"] = new_output_path
    if baseline_output_path:
        for p in pairs:
            p["run1"] = baseline_output_path

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        for pair in pairs:
            tumor = pair["tumor"]
            normal = pair["normal"]
            subject = pair["metadata"].get("subject", f"{tumor}_{normal}")

            logger.info(f"Processing {subject} ({tumor}/{normal})")

            run1_dir = tmp_path / "baseline" / f"{tumor}_{normal}"
            run2_dir = tmp_path / "new" / f"{tumor}_{normal}"
            download_s3_dir(pair["run1"], run1_dir)
            download_s3_dir(pair["run2"], run2_dir)

            schema_run1 = check_schema(run1_dir, tumor, normal)
            schema_run2 = check_schema(run2_dir, tumor, normal)

            schema_result = {
                "baseline": schema_run1,
                "new": schema_run2,
                "passed": schema_run1["passed"] and schema_run2["passed"],
            }

            if not schema_result["passed"]:
                logger.error(f"Schema check failed for {subject} — skipping comparison")
                results.append({"subject": subject, "schema": schema_result, "comparison": None})
                continue

            output_dir = tmp_path / "output" / f"{tumor}_{normal}"
            comparison_result = run_comparison(run1_dir, run2_dir, tumor, normal, output_dir)

            exec_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            case_id = pair["metadata"].get("case", f"{tumor}_{normal}")
            s3_out_prefix = f"{RESULT_S3_PREFIX.rstrip('/')}/{new_version}-vs-{baseline_version}/{case_id}/{exec_id}/test/"
            for f in output_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(output_dir)
                    upload_file(f, f"{s3_out_prefix}data/{rel}")

            results.append({
                "subject": subject,
                "schema": schema_result,
                "comparison": comparison_result,
                "s3_results": s3_out_prefix,
            })

    summary = {
        "new_version": new_version,
        "baseline_version": baseline_version,
        "results": results,
        "all_schema_passed": all(r["schema"]["passed"] for r in results),
    }

    logger.info(f"Done: {json.dumps(summary, default=str)}")
    return summary
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd app && pytest tests/ -v
```

Expected: all tests PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add app/comparator/lambdas/comparator/handler.py app/tests/test_comparator_handler.py
git commit -m "feat(comparator): accept new_output_path/baseline_output_path overrides from Watcher"
```

---

## Task 2: DynamoDB helpers

**Files:**

- Create: `app/submitter/__init__.py`
- Create: `app/submitter/db.py`
- Create: `app/watcher/__init__.py`
- Create: `app/watcher/db.py`
- Create: `app/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_db.py`:

```python
import os
import time
import pytest
import boto3
from moto import mock_aws

os.environ.setdefault("REGRESSION_TABLE_NAME", "sash-regression-runs-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from submitter.db import put_run, get_run, update_run_status


@pytest.fixture
def ddb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ap-southeast-2")
        client.create_table(
            TableName="sash-regression-runs-test",
            KeySchema=[{"AttributeName": "portal_run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "portal_run_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def test_put_and_get_run(ddb_table):
    put_run("run-abc", "0.7.1", "0.6.4", "s3://bucket/baseline/")
    item = get_run("run-abc")
    assert item["portal_run_id"] == "run-abc"
    assert item["new_version"] == "0.7.1"
    assert item["baseline_version"] == "0.6.4"
    assert item["baseline_output_path"] == "s3://bucket/baseline/"
    assert item["status"] == "SUBMITTED"
    assert "submitted_at" in item
    assert item["ttl"] > int(time.time())


def test_get_run_returns_none_for_unknown(ddb_table):
    assert get_run("not-tracked") is None


def test_update_run_status(ddb_table):
    put_run("run-xyz", "0.7.1", "0.6.4", "s3://bucket/baseline/")
    update_run_status("run-xyz", "SUCCEEDED")
    assert get_run("run-xyz")["status"] == "SUCCEEDED"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd app && pytest tests/test_db.py -v
```

Expected: FAIL — `submitter.db` module not found.

- [ ] **Step 3: Create the module files**

Create `app/submitter/__init__.py` (empty).

Create `app/submitter/db.py`:

```python
import os
import time
from datetime import datetime, timezone

import boto3

_TABLE_NAME = os.environ["REGRESSION_TABLE_NAME"]


def _table():
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2")).Table(_TABLE_NAME)


def put_run(portal_run_id: str, new_version: str, baseline_version: str, baseline_output_path: str) -> None:
    _table().put_item(Item={
        "portal_run_id": portal_run_id,
        "new_version": new_version,
        "baseline_version": baseline_version,
        "baseline_output_path": baseline_output_path,
        "status": "SUBMITTED",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "ttl": int(time.time()) + 30 * 24 * 3600,
    })


def get_run(portal_run_id: str) -> dict | None:
    resp = _table().get_item(Key={"portal_run_id": portal_run_id})
    return resp.get("Item")


def update_run_status(portal_run_id: str, status: str) -> None:
    _table().update_item(
        Key={"portal_run_id": portal_run_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
    )
```

Create `app/watcher/__init__.py` (empty).

Create `app/watcher/db.py` with identical content to `app/submitter/db.py`.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd app && pytest tests/test_db.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/submitter/ app/watcher/__init__.py app/watcher/db.py app/tests/test_db.py
git commit -m "feat: add DynamoDB helpers for regression run tracking"
```

---

## Task 3: Submitter Lambda

**Files:**

- Create: `app/submitter/submit.py`
- Create: `app/submitter/lambdas/__init__.py`
- Create: `app/submitter/lambdas/submitter/__init__.py`
- Create: `app/submitter/lambdas/submitter/handler.py`
- Create: `app/tests/test_submitter_handler.py`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_submitter_handler.py`:

```python
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("REGRESSION_TABLE_NAME", "sash-regression-runs-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")

from submitter.lambdas.submitter.handler import handler


def _api_event(body: dict) -> dict:
    return {"body": json.dumps(body)}


def test_returns_portal_run_id_on_success():
    with (
        patch("submitter.lambdas.submitter.handler.submit_sash_run", return_value="run-123"),
        patch("submitter.lambdas.submitter.handler.put_run"),
    ):
        result = handler(_api_event({"new_version": "0.7.1", "baseline_version": "0.6.4", "baseline_output_path": "s3://b/p/"}), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["portal_run_id"] == "run-123"


def test_put_run_called_with_correct_args():
    with (
        patch("submitter.lambdas.submitter.handler.submit_sash_run", return_value="run-456"),
        patch("submitter.lambdas.submitter.handler.put_run") as mock_put,
    ):
        handler(_api_event({"new_version": "0.8.0", "baseline_version": "0.6.4", "baseline_output_path": "s3://b/bl/"}), None)

    mock_put.assert_called_once_with("run-456", "0.8.0", "0.6.4", "s3://b/bl/")


def test_missing_new_version_raises():
    with pytest.raises(KeyError):
        handler(_api_event({"baseline_output_path": "s3://b/bl/"}), None)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd app && pytest tests/test_submitter_handler.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create the Submitter files**

Create `app/submitter/submit.py`:

```python
"""
OrcaBus workflow submission for sash regression runs.

NOTE(QC): Confirm submission mechanism with Florian — options are:
  a) PUT a WorkflowRunStateChange DRAFT event on the OrcaBusMain event bus
     (triggers existing service-sash-pipeline-manager)
  b) Call the OrcaBus workflow manager HTTP API directly

Returns portal_run_id once confirmed.
"""


def submit_sash_run(new_version: str) -> str:
    raise NotImplementedError(
        "OrcaBus submission not yet wired — confirm mechanism with Florian. "
        "See app/submitter/submit.py for options."
    )
```

Create `app/submitter/lambdas/__init__.py` (empty).
Create `app/submitter/lambdas/submitter/__init__.py` (empty).

Create `app/submitter/lambdas/submitter/handler.py`:

```python
import json
import logging

from submitter.db import put_run
from submitter.submit import submit_sash_run

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    body = json.loads(event.get("body") or "{}")
    new_version = body["new_version"]
    baseline_version = body["baseline_version"]
    baseline_output_path = body["baseline_output_path"]

    logger.info(f"Submitting sash regression: new={new_version} vs baseline={baseline_version}")

    portal_run_id = submit_sash_run(new_version)
    put_run(portal_run_id, new_version, baseline_version, baseline_output_path)

    logger.info(f"Tracked portal_run_id={portal_run_id}")
    return {
        "statusCode": 200,
        "body": json.dumps({"portal_run_id": portal_run_id}),
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd app && pytest tests/test_submitter_handler.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/submitter/ app/tests/test_submitter_handler.py
git commit -m "feat: add Submitter Lambda (OrcaBus submission stub — pending Florian)"
```

---

## Task 4: Watcher Lambda

**Files:**

- Create: `app/watcher/track.py`
- Create: `app/watcher/lambdas/__init__.py`
- Create: `app/watcher/lambdas/watcher/__init__.py`
- Create: `app/watcher/lambdas/watcher/handler.py`
- Create: `app/tests/test_watcher_handler.py`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_watcher_handler.py`:

```python
import json
import os
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("REGRESSION_TABLE_NAME", "sash-regression-runs-test")
os.environ.setdefault("COMPARATOR_FUNCTION_NAME", "sash-regression-comparator")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")

from watcher.lambdas.watcher.handler import handler

_TRACKED_RUN = {
    "portal_run_id": "run-abc",
    "new_version": "0.7.1",
    "baseline_version": "0.6.4",
    "baseline_output_path": "s3://bucket/baseline/",
    "status": "SUBMITTED",
}


def _eb_event(portal_run_id: str, status: str, output_dir: str = "s3://cache/sash/run-abc/") -> dict:
    return {
        "detail": {
            "portalRunId": portal_run_id,
            "status": status,
            "workflow": {"name": "sash", "version": "0.7.1"},
            "payload": {"data": {"outputs": {"outputDirectory": output_dir}}},
        }
    }


def test_invokes_comparator_on_succeeded():
    with (
        patch("watcher.lambdas.watcher.handler.get_run", return_value=_TRACKED_RUN),
        patch("watcher.lambdas.watcher.handler.update_run_status"),
        patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock_invoke,
    ):
        handler(_eb_event("run-abc", "SUCCEEDED"), None)

    mock_invoke.assert_called_once_with(
        new_version="0.7.1",
        baseline_version="0.6.4",
        baseline_output_path="s3://bucket/baseline/",
        new_output_path="s3://cache/sash/run-abc/",
    )


def test_ignores_untracked_run():
    with (
        patch("watcher.lambdas.watcher.handler.get_run", return_value=None),
        patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock_invoke,
    ):
        handler(_eb_event("run-not-tracked", "SUCCEEDED"), None)

    mock_invoke.assert_not_called()


def test_updates_status_on_failed():
    with (
        patch("watcher.lambdas.watcher.handler.get_run", return_value=_TRACKED_RUN),
        patch("watcher.lambdas.watcher.handler.update_run_status") as mock_update,
        patch("watcher.lambdas.watcher.handler.invoke_comparator"),
    ):
        handler(_eb_event("run-abc", "FAILED"), None)

    mock_update.assert_called_once_with("run-abc", "FAILED")


def test_does_not_invoke_comparator_on_failed():
    with (
        patch("watcher.lambdas.watcher.handler.get_run", return_value=_TRACKED_RUN),
        patch("watcher.lambdas.watcher.handler.update_run_status"),
        patch("watcher.lambdas.watcher.handler.invoke_comparator") as mock_invoke,
    ):
        handler(_eb_event("run-abc", "FAILED"), None)

    mock_invoke.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd app && pytest tests/test_watcher_handler.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create the Watcher files**

Create `app/watcher/track.py`:

```python
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

COMPARATOR_FUNCTION_NAME = os.environ["COMPARATOR_FUNCTION_NAME"]


def invoke_comparator(new_version: str, baseline_version: str, baseline_output_path: str, new_output_path: str) -> None:
    payload = {
        "new_version": new_version,
        "baseline_version": baseline_version,
        "new_output_path": new_output_path,
        "baseline_output_path": baseline_output_path,
    }
    boto3.client("lambda").invoke(
        FunctionName=COMPARATOR_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
    logger.info(f"Invoked comparator async: {payload}")
```

Create `app/watcher/lambdas/__init__.py` (empty).
Create `app/watcher/lambdas/watcher/__init__.py` (empty).

Create `app/watcher/lambdas/watcher/handler.py`:

```python
import logging

from watcher.db import get_run, update_run_status
from watcher.track import invoke_comparator

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> None:
    detail = event["detail"]
    portal_run_id = detail["portalRunId"]
    status = detail["status"]

    logger.info(f"WorkflowRunStateChange: portal_run_id={portal_run_id} status={status}")

    run = get_run(portal_run_id)
    if run is None:
        logger.info(f"portal_run_id={portal_run_id} not tracked — ignoring")
        return

    update_run_status(portal_run_id, status)

    if status == "SUCCEEDED":
        # NOTE(QC): verify outputDirectory field name against a real SUCCEEDED event
        new_output_path = detail["payload"]["data"]["outputs"]["outputDirectory"]
        invoke_comparator(
            new_version=run["new_version"],
            baseline_version=run["baseline_version"],
            baseline_output_path=run["baseline_output_path"],
            new_output_path=new_output_path,
        )
    elif status == "FAILED":
        logger.warning(f"Sash run FAILED for portal_run_id={portal_run_id} — no comparison")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd app && pytest tests/test_watcher_handler.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd app && pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/watcher/ app/tests/test_watcher_handler.py
git commit -m "feat: add Watcher Lambda — DynamoDB lookup + async Comparator invocation"
```

---

## Task 5: CDK infrastructure

**Files:**

- Modify: `infrastructure/stage/constants.ts`
- Modify: `infrastructure/stage/deployment-stack.ts`

- [ ] **Step 1: Update constants**

In `infrastructure/stage/constants.ts`, add after the existing constants:

```typescript
export const REGRESSION_TABLE_NAME = 'sash-regression-runs';
export const EVENT_BUS_NAME = 'OrcaBusMain';
export const WORKFLOW_MANAGER_SOURCE = 'orcabus.workflowmanager';
export const WORKFLOW_RUN_STATE_CHANGE = 'WorkflowRunStateChange';
```

- [ ] **Step 2: Update deployment-stack.ts imports**

Replace the top of `infrastructure/stage/deployment-stack.ts` with:

```typescript
import * as path from 'path';
import { Construct } from 'constructs';
import {
  DockerImageCode,
  DockerImageFunction,
  Function,
  Runtime,
  Code,
  Architecture,
} from 'aws-cdk-lib/aws-lambda';
import {
  aws_lambda,
  aws_dynamodb,
  aws_events,
  aws_events_targets,
  Duration,
  Size,
  Stack,
  StackProps,
  RemovalPolicy,
} from 'aws-cdk-lib';
import { HttpApi, HttpMethod } from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import { ManagedPolicy, PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import {
  APP_ROOT,
  TESTDATA_BUCKET,
  RESULTS_BUCKET,
  REGRESSION_TABLE_NAME,
  EVENT_BUS_NAME,
  WORKFLOW_MANAGER_SOURCE,
  WORKFLOW_RUN_STATE_CHANGE,
  getStageConstants,
} from './constants';
import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';
```

- [ ] **Step 3: Add DynamoDB table and Watcher/Submitter to the constructor**

In `SashRegressionStack.constructor`, after `this.createComparatorFunction(...)`, add:

```typescript
const comparatorFn = this.createComparatorFunction(testdataConfigS3Uri, resultS3Prefix);
const table = this.createRegressionTable();
this.createSubmitterFunction(table);
this.createWatcherFunction(table, comparatorFn);
```

Update `createComparatorFunction` to return the function:

```typescript
private createComparatorFunction(testdataConfigS3Uri: string, resultS3Prefix: string): DockerImageFunction {
  const fn = new DockerImageFunction(this, 'ComparatorFunction', {
    code: DockerImageCode.fromImageAsset(path.join(APP_ROOT)),
    architecture: aws_lambda.Architecture.ARM_64,
    timeout: Duration.minutes(15),
    memorySize: 4096,
    ephemeralStorageSize: Size.gibibytes(10),
    role: this.lambdaRole,
    environment: {
      TESTDATA_CONFIG_S3_URI: testdataConfigS3Uri,
      RESULT_S3_PREFIX: resultS3Prefix,
    },
  });
  return fn;
}
```

- [ ] **Step 4: Add the three new private methods**

Add to `SashRegressionStack`:

```typescript
private createRegressionTable(): aws_dynamodb.Table {
  return new aws_dynamodb.Table(this, 'RegressionRunsTable', {
    tableName: REGRESSION_TABLE_NAME,
    partitionKey: { name: 'portal_run_id', type: aws_dynamodb.AttributeType.STRING },
    billingMode: aws_dynamodb.BillingMode.PAY_PER_REQUEST,
    timeToLiveAttribute: 'ttl',
    removalPolicy: RemovalPolicy.RETAIN,
  });
}

private createSubmitterFunction(table: aws_dynamodb.Table): void {
  const submitterRole = new Role(this, 'SubmitterRole', {
    assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
  });
  submitterRole.addManagedPolicy(
    ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
  );
  table.grantWriteData(submitterRole);

  const fn = new Function(this, 'SubmitterFunction', {
    runtime: Runtime.PYTHON_3_13,
    architecture: Architecture.ARM_64,
    code: Code.fromAsset(path.join(APP_ROOT, 'submitter')),
    handler: 'lambdas.submitter.handler.handler',
    timeout: Duration.seconds(30),
    role: submitterRole,
    environment: {
      REGRESSION_TABLE_NAME: REGRESSION_TABLE_NAME,
    },
  });

  const api = new HttpApi(this, 'RegressionApi', {
    apiName: 'sash-regression-api',
  });
  api.addRoutes({
    path: '/regression/submit',
    methods: [HttpMethod.POST],
    integration: new HttpLambdaIntegration('SubmitterIntegration', fn),
  });
}

private createWatcherFunction(table: aws_dynamodb.Table, comparatorFn: DockerImageFunction): void {
  const watcherRole = new Role(this, 'WatcherRole', {
    assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
  });
  watcherRole.addManagedPolicy(
    ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
  );
  table.grantReadWriteData(watcherRole);
  comparatorFn.grantInvoke(watcherRole);

  const fn = new Function(this, 'WatcherFunction', {
    runtime: Runtime.PYTHON_3_13,
    architecture: Architecture.ARM_64,
    code: Code.fromAsset(path.join(APP_ROOT, 'watcher')),
    handler: 'lambdas.watcher.handler.handler',
    timeout: Duration.seconds(30),
    role: watcherRole,
    environment: {
      REGRESSION_TABLE_NAME: REGRESSION_TABLE_NAME,
      COMPARATOR_FUNCTION_NAME: comparatorFn.functionName,
    },
  });

  const eventBus = aws_events.EventBus.fromEventBusName(this, 'OrcaBusMain', EVENT_BUS_NAME);
  new aws_events.Rule(this, 'SashWatcherRule', {
    eventBus,
    eventPattern: {
      source: [WORKFLOW_MANAGER_SOURCE],
      detailType: [WORKFLOW_RUN_STATE_CHANGE],
      detail: {
        workflow: { name: ['sash'] },
        status: ['SUCCEEDED', 'FAILED'],
      },
    },
    targets: [new aws_events_targets.LambdaFunction(fn)],
  });
}
```

- [ ] **Step 5: Install CDK dependencies if needed**

```bash
cd infrastructure && npm install aws-cdk-lib @aws-cdk/aws-apigatewayv2-alpha 2>/dev/null || true
npx tsc --noEmit
```

Expected: no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/stage/constants.ts infrastructure/stage/deployment-stack.ts
git commit -m "feat(infra): add DynamoDB table, Submitter + Watcher Lambdas, EventBridge rule, HTTP API"
```

---

## Task 6: Makefile targets

**Files:**

- Modify: `app/Makefile`

- [ ] **Step 1: Add submitter and watcher targets**

In `app/Makefile`, add after the existing `invoke-local` target:

```makefile
# Run full test suite including submitter + watcher
test:
	pytest tests/ --cov=comparator --cov=submitter --cov=watcher --cov-report=term-missing

# Invoke Submitter locally (requires OrcaBus submission to be implemented)
invoke-submitter:
	REGRESSION_TABLE_NAME=sash-regression-runs \
	AWS_PROFILE=umccr-dev-pu \
	python -c "
from submitter.lambdas.submitter.handler import handler
import json
print(json.dumps(handler({
  'body': json.dumps({
    'new_version': '0.7.1',
    'baseline_output_path': 's3://umccr-research-dev/quentin/sash-regression/testdata/run1/L2301218__L2301217/'
  })
}, None), indent=2))
"
```

- [ ] **Step 2: Run full test suite**

```bash
cd app && make test
```

Expected: all tests PASS with coverage report showing submitter + watcher modules.

- [ ] **Step 3: Commit**

```bash
git add app/Makefile
git commit -m "chore: update Makefile — full coverage + invoke-submitter target"
```

---

## Verification

1. `cd app && make test` — all tests pass
2. `cd infrastructure && npx tsc --noEmit` — no TypeScript errors
3. After Florian meeting: fill in `app/submitter/submit.py` with the confirmed OrcaBus submission mechanism
4. Integration test: `make invoke-submitter` once submission is wired, verify DynamoDB record appears, verify Comparator is invoked when the sash run completes
