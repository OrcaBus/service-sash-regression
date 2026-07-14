# Comparison completion notification — design

Date: 2026-07-08
Status: approved, ready for planning

## Problem

The Comparator logs `FINAL_RESULT` to CloudWatch and writes per-pair results to S3, but
nothing tells anyone a comparison finished. Operators have to poll CloudWatch or S3
manually to learn whether a sash regression run passed. This is priority items #2 and #3
from the service assessment: emit a completion event, and notify Slack from it.

## Scope

1. Comparator emits a `SashRegressionComparisonCompleted` EventBridge event after building
   `compact_summary`.
2. A new Notifier Lambda, triggered by that event, posts a PASS/WARN/FAIL/MANUAL_CHECK
   summary to Slack via an incoming webhook.

Out of scope (tracked separately, not part of this work):

- WRU validator error-body hardening (#1)
- Per-pair fan-out / Step Functions (#4)
- Unit tests for `comprehensive_sash_comparison.py` parsers (#5)
- Per-stage `wruDraftValidatorFunctionName` (#6)

## 1. Event: `SashRegressionComparisonCompleted`

### Shared `exec_id` per comparison run

Today `exec_id` is generated per-pair inside `_run_pair` (`handler.py:152`), so pairs
processed in the same Lambda invocation can land under different timestamp prefixes in S3
and there is no single location representing "this comparison run."

Fix: generate `exec_id` once in `handler()` and thread it into every `_run_pair` call. All
pairs from one invocation then share one S3 prefix:
`{RESULT_S3_PREFIX}/{new_version}-vs-{baseline_version}/{exec_id}/`

### Run-level summary upload

After all pairs complete and `compact_summary` is built, upload one aggregate object:

`{RESULT_S3_PREFIX}/{new_version}-vs-{baseline_version}/{exec_id}/run_summary.json`

containing `compact_summary` plus each pair's `subject` and `summary_s3_uri`. This is the
one link Slack and the event point to, regardless of how many pairs ran.

### Event emission

Mirror the existing pattern in `submit.py::_emit_submitted_event` (same file already emits
`SashRegressionRunSubmitted` via `boto3.client("events").put_events`, `EventBusName` from an
env var).

```python
def _emit_completed_event(new_version, baseline_version, compact_summary, run_summary_s3_uri):
    events = boto3.client("events")
    events.put_events(Entries=[{
        "Source": "sash-regression.comparator",
        "DetailType": "SashRegressionComparisonCompleted",
        "Detail": json.dumps({
            "newVersion": new_version,
            "baselineVersion": baseline_version,
            "status": compact_summary["status"],
            "totalPairs": compact_summary["total_pairs"],
            "passCount": compact_summary["pass_count"],
            "warnCount": compact_summary["warn_count"],
            "failCount": compact_summary["fail_count"],
            "manualCheckCount": compact_summary["manual_check_count"],
            "criticalItems": compact_summary["critical_items"],
            "warningItems": compact_summary["warning_items"],
            "resultS3Uri": run_summary_s3_uri,
        }),
        "EventBusName": EVENTS_BUS_NAME,
    }])
```

`EVENTS_BUS_NAME` is a new env var on the Comparator function (same `EVENT_BUS_NAME`
constant already used by the Submitter). Called as the last step of `handler()`, after all
S3 uploads succeed — if `put_events` raises, results are already durably in S3; only the
notification is lost, and the Lambda invocation shows as failed in CloudWatch (visible, not
silent).

## 2. Notifier Lambda

New app module, structured like the existing `watcher/`:

```
app/notifier/
  __init__.py
  slack.py                        # message building + posting
  lambdas/notifier/handler.py      # EventBridge entrypoint
```

### `slack.py`

```python
STATUS_EMOJI = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "MANUAL_CHECK": "🔎"}

def build_message(detail: dict) -> dict:
    emoji = STATUS_EMOJI.get(detail["status"], "❓")
    lines = [
        f"{emoji} *sash regression: {detail['newVersion']} vs {detail['baselineVersion']}* — {detail['status']}",
        f"Pairs: {detail['passCount']} pass / {detail['warnCount']} warn / "
        f"{detail['failCount']} fail / {detail['manualCheckCount']} manual_check",
    ]
    if detail.get("criticalItems"):
        lines.append(f"Critical: {', '.join(detail['criticalItems'])}")
    if detail.get("warningItems"):
        lines.append(f"Warnings: {', '.join(detail['warningItems'])}")
    lines.append(f"Results: {_s3_console_url(detail['resultS3Uri'])}")
    return {"text": "\n".join(lines)}

def post_to_slack(webhook_url: str, message: dict) -> None:
    resp = requests.post(webhook_url, json=message, timeout=10)
    resp.raise_for_status()
```

`_s3_console_url` converts `s3://bucket/key` to an S3 console URL so the link opens the
object directly in a browser.

### `handler.py`

```python
SLACK_WEBHOOK_SECRET_ID = os.environ["SLACK_WEBHOOK_SECRET_ID"]

def handler(event: dict, context) -> None:
    detail = event["detail"]
    webhook_url = _get_webhook_url()  # cached module-level, same pattern as submit.py's _orcabus_token()
    post_to_slack(webhook_url, build_message(detail))
```

Notifies on **every** completion (PASS/WARN/FAIL/MANUAL_CHECK) — a visible heartbeat that
regression testing ran, not just an alert on failure.

### Slack delivery mechanism

Incoming webhook URL stored in Secrets Manager (`SLACK_WEBHOOK_SECRET_ID`, placeholder
value in `constants.ts` — e.g. `sash-regression/slack-webhook-url`), same lifecycle as
`ORCABUS_TOKEN_SECRET_ID`: created and populated manually post-deploy. This was chosen over:

- **Reusing the existing `AwsChatBotTopic-alerts` SNS topic** — that topic is wired
  exclusively to AWS Chatbot's native CloudWatch Alarm formatting (see
  `orcahouse/infra/service-event-ingestion/monitor.tf`); a custom PASS/FAIL payload
  wouldn't render through it without faking an alarm state-change, and it's cross-account
  from sash-regression's account.
- **Slack bot token + `chat.postMessage`** — more flexible but needs a Slack app with
  `chat:write` scope; unnecessary for a single fixed-channel notification.

No webhook exists yet for this purpose — the secret name is a placeholder until an actual
webhook is provisioned and confirmed before deploy.

## 3. CDK wiring (`deployment-stack.ts`)

- New `createNotifierFunction(mainBus)`, following the existing per-Lambda-role pattern:
  - Role: `secretsmanager:GetSecretValue` scoped to the new `SLACK_WEBHOOK_SECRET_ID` ARN
  - `DockerImageFunction` with `cmd: ['notifier.lambdas.notifier.handler.handler']`
  - Env: `SLACK_WEBHOOK_SECRET_ID`
- New `Rule`:
  ```ts
  new Rule(this, 'NotifierRule', {
    eventBus: mainBus,
    eventPattern: {
      source: ['sash-regression.comparator'],
      detailType: ['SashRegressionComparisonCompleted'],
    },
    targets: [new LambdaFunction(notifierFn)],
  });
  ```
- Comparator's existing role gains `events:PutEvents` on `mainBus.eventBusArn` (same
  statement already present on the Submitter role)
- Comparator function gains `EVENTS_BUS_NAME` env var

## 4. Testing

- `test_comparator_handler.py`:
  - `exec_id` generated once per `handler()` invocation and shared across all pairs
  - run-level `run_summary.json` uploaded with correct content and S3 key
  - `_emit_completed_event` called with the expected `Detail` (mock `boto3.client("events")`)
- New `app/tests/test_notifier_handler.py`:
  - `build_message` output for each status (PASS/WARN/FAIL/MANUAL_CHECK), including
    critical/warning item formatting and S3 console URL conversion
  - `post_to_slack` call args (mock `requests.post`)
  - `handler` wires an EventBridge `detail` through to a posted message

## Error handling

- `put_events` failure in the Comparator: let it raise. All S3 writes already completed by
  that point; only the notification is lost, and the failure is visible as a Lambda error.
- Slack delivery failure in the Notifier: let it raise. EventBridge's default Lambda-target
  retry policy handles transient failures; persistent failures surface as a Lambda error
  metric, which is strictly more visible than the current no-notification state.
