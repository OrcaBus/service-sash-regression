# Implementation Plan: Sash Regression Service — Phase 3 (Publisher)

Derived from `design.md` and `requirements.md`. Each task references the requirement(s) it
satisfies.

- [ ] 1. Comparator: shared ExecId and run-level summary

  - Generate a single `exec_id` once in `handler()` and thread it into every `_run_pair` call so
    all pairs from one invocation land under one S3 prefix
  - After all pairs complete, assemble and upload one aggregate `run_summary.json` to
    `{RESULT_S3_PREFIX}/{new_version}-vs-{baseline_version}/{exec_id}/`
  - _Requirements: 1.1, 1.2_

- [ ] 2. Comparator: emit `SashRegressionComparisonCompleted` event

  - Add `_emit_completed_event(new_version, baseline_version, portal_run_id, run_summary, result_s3_prefix)`
    per `design.md`, called after the `run_summary.json` upload succeeds
  - Thread `portal_run_id` through from the Watcher's invocation payload
  - Let `put_events` exceptions propagate (S3 writes already durable; only the notification is
    lost, failure visible in CloudWatch)
  - Add `EVENTS_BUS_NAME` environment variable (mirrors existing `EVENT_BUS_NAME` on Submitter)
  - _Requirements: 1.3, 1.4_

- [ ] 3. Comparator tests

  - Extend `test_comparator_handler.py`: ExecId generated once and shared across pairs;
    `run_summary.json` uploaded with correct content and S3 key; `_emit_completed_event` called
    with the expected `Detail` (mock `boto3.client("events")`)
  - _Requirements: 5.1_

- [ ] 4. Publisher module scaffold

  - Create `app/publisher/` following the `watcher/` structure: `__init__.py`, `slack.py`,
    `github.py`, `lambdas/publisher/handler.py`
  - _Requirements: 2, 3, 4.1_

- [ ] 5. Publisher: Slack delivery

  - `build_slack_message(detail)`: map `outcome` to emoji (`PASS`→✅, `WARN`→⚠️, `FAIL`→❌,
    `MANUAL_CHECK`→🔎); include outcome, per-status pair counts, critical/warning items (if
    present), S3 results link
  - `post_to_slack(webhook_url, message)`: POST via `requests`, raise on non-2xx
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 6. Publisher: GitHub delivery

  - `build_github_issue(detail)`: return `(title, body)` — title includes outcome + version
    comparison; body includes per-status pair counts, critical items (if present), S3 results
    link, and `https://github.com/umccr/sash/tree/release/{newVersion}` link
  - `post_to_github(token, repo, title, body)`: `POST /repos/{repo}/issues`, raise
    `RuntimeError` on non-2xx
  - _Requirements: 3.1, 3.2, 3.3_

- [ ] 7. Publisher: handler wiring and independent delivery

  - `handler(event, context)`: retrieve Slack webhook URL (`SLACK_WEBHOOK_SECRET_ID`) and GitHub
    token (`GITHUB_TOKEN_SECRET_ID`) from Secrets Manager, attempt both posts
  - Wrap each post in try/except so a Slack failure doesn't block the GitHub post or vice versa;
    log both outcomes; re-raise a combined error if either failed (EventBridge retry still
    applies)
  - _Requirements: 2.1, 3.1, 3.4_

- [ ] 8. Publisher tests

  - `test_publisher_slack.py`: `build_slack_message` output for each of the four outcomes
    including critical/warning item formatting and S3 console URL conversion; `post_to_slack`
    call args (mock `requests.post`)
  - `test_publisher_github.py`: `build_github_issue` title/body for each outcome including the
    `release/<version>` branch link; `post_to_github` call args (mock the GitHub API client),
    raises on non-2xx
  - `test_publisher_handler.py`: both posts attempted even if one raises; combined error surfaced
  - _Requirements: 5.2, 5.3, 5.4_

- [ ] 9. CDK: Publisher Lambda construct

  - Add `createPublisherFunction(mainBus, slackWebhookSecretId, githubTokenSecretId)` to
    `infrastructure/stage/deployment-stack.ts`: `DockerImageFunction` with
    `cmd: ['publisher.lambdas.publisher.handler.handler']`, dedicated IAM role
  - _Requirements: 4.1_

- [ ] 10. CDK: EventBridge rule

  - New `Rule` on `mainBus` matching `source: ['sash-regression.comparator']`,
    `detailType: ['SashRegressionComparisonCompleted']`, targeting the Publisher Lambda
  - _Requirements: 4.2_

- [ ] 11. CDK: IAM scoping

  - `PublisherRole`: `secretsmanager:GetSecretValue` scoped to exactly the Slack webhook secret
    ARN and the GitHub token secret ARN — no wildcard
  - `ComparatorRole`: add `events:PutEvents` on `mainBus.eventBusArn` (same statement pattern as
    Submitter)
  - _Requirements: 4.3, 4.4_

- [ ] 12. CDK: per-stage secret-ID constants and env vars

  - `infrastructure/stage/constants.ts`: add `SLACK_WEBHOOK_SECRET_ID` and
    `GITHUB_TOKEN_SECRET_ID` per-stage maps (BETA/GAMMA/PROD), following the existing
    `WRU_VALIDATOR_FUNCTION_NAME` pattern; wire into `getStageConstants`
  - Supply `SLACK_WEBHOOK_SECRET_ID` and `GITHUB_TOKEN_SECRET_ID` env vars to the Publisher
    Lambda, and `EVENTS_BUS_NAME` to the Comparator
  - _Requirements: 4.5_

- [ ] 13. CDK snapshot tests

  - Extend `test/stage.test.ts` to verify the Publisher Lambda and its EventBridge rule exist;
    confirm `cdk-nag` passes with no new suppressions
  - _Requirements: 4_

- [ ] 14. Manual pre-deploy setup (not code — tracked here so it isn't missed)
  - Create `sash-regression/github-token` secret in Secrets Manager (fine-grained PAT or GitHub
    App token, scoped to `umccr/sash` issue write)
  - Create `sash-regression/slack-webhook{-beta,-gamma,}` secrets once the Incoming Webhook URL
    is provisioned and confirmed
  - _Requirements: (infrastructure prerequisite, not a numbered requirement)_
