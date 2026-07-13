# Requirements Document

## Introduction

The Sash Regression Service validates `sash` bioinformatics pipeline releases by comparing
outputs (VCFs, TSVs, Purple metrics, BCFtools stats) from a new version against a curated
baseline. This document captures the requirements for **Phase 3 (Publisher)**: the Comparator
emits a completion event, and a new PublisherFunction posts the result to GitHub and Slack. Per
`Daily/2026-07-11.md`, this phase is scoped narrowly (~3-4 days) and explicitly excludes
per-pair fan-out, the comparison-engine refactor, and unrelated security hardening — those are
tracked separately.

## Glossary

- **ComparatorFunction**: Existing Lambda that runs the schema check and metric comparison for
  each configured pair, then (as of this phase) emits a `SashRegressionComparisonCompleted`
  event.
- **PublisherFunction**: Lambda that receives `SashRegressionComparisonCompleted` and posts a
  result summary to both GitHub (PR comment) and Slack (Incoming Webhook).
- **ExecId**: Identifier generated once per Comparator invocation, shared across all pairs in
  that run, used as the S3 prefix for the run-level summary.
- **RunSummary**: The aggregate JSON object (`run_summary.json`) covering all pairs processed in
  one Comparator invocation.
- **Outcome**: The comparison result for a run: `PASS`, `WARN`, `FAIL`, or `MANUAL_CHECK`.
- **OrcaBusMain**: The EventBridge event bus used by the OrcaBus platform.

---

## Requirements

### Requirement 1: Comparator Completion Event

**User Story:** As an operator, I want the Comparator to emit an event when a comparison run
finishes, so that downstream consumers (Publisher) know a result is ready without polling S3.

#### Acceptance Criteria

1. THE ComparatorFunction SHALL generate a single ExecId per `handler()` invocation and thread
   it into every pair comparison, so all pairs from one invocation share one S3 prefix.
2. WHEN all pairs in an invocation complete, THE ComparatorFunction SHALL upload one aggregate
   `run_summary.json` to `{RESULT_S3_PREFIX}/{new_version}-vs-{baseline_version}/{execId}/`.
3. WHEN the RunSummary is uploaded, THE ComparatorFunction SHALL emit a
   `SashRegressionComparisonCompleted` event to OrcaBusMain with source
   `sash-regression.comparator`, containing `newVersion`, `baselineVersion`, `portalRunId`,
   `outcome`, `resultS3Prefix`, and `metricSummary` (pass/warn/fail/manual-check counts plus
   critical/warning items).
4. IF `put_events` raises, THEN THE ComparatorFunction SHALL let the exception propagate — S3
   writes have already completed, so only the notification is lost, and the Lambda invocation
   fails visibly in CloudWatch.

---

### Requirement 2: PublisherFunction — Slack

**User Story:** As an operator, I want a Slack notification when a regression comparison
completes, so that I know immediately whether the new sash version passes or fails.

#### Acceptance Criteria

1. WHEN the PublisherFunction receives a `SashRegressionComparisonCompleted` event, THE
   PublisherFunction SHALL retrieve the Slack Incoming Webhook URL from Secrets Manager using
   `SLACK_WEBHOOK_SECRET_ID`.
2. THE PublisherFunction SHALL map `outcome` to emoji: `PASS` → ✅, `WARN` → ⚠️, `FAIL` → ❌,
   `MANUAL_CHECK` → 🔎.
3. THE PublisherFunction SHALL notify Slack on every completion (PASS/WARN/FAIL/MANUAL_CHECK),
   not only failures.
4. THE Slack message SHALL include the outcome, per-status pair counts, critical/warning items
   (if present), and a link to the results in S3.
5. WHEN the Slack webhook POST returns a non-2xx HTTP status, THE PublisherFunction SHALL raise
   an error, causing the Lambda to fail and trigger EventBridge's at-least-once retry.

---

### Requirement 3: PublisherFunction — GitHub

**User Story:** As an operator, I want the comparison result posted to the relevant sash GitHub
PR, so that the release decision-maker sees regression status without leaving GitHub.

#### Acceptance Criteria

1. WHEN the PublisherFunction receives a `SashRegressionComparisonCompleted` event, THE
   PublisherFunction SHALL retrieve a GitHub token from Secrets Manager using
   `GITHUB_TOKEN_SECRET_ID`.
2. THE PublisherFunction SHALL locate the open sash PR for `newVersion` by querying
   `GET /repos/umccr/sash/pulls?head=umccr:release/{newVersion}&state=open` (verified
   convention across every sash release in git history: 0.6.0, 0.6.1, 0.6.2, 0.6.4, 0.7.0 —
   see PR [#39](https://github.com/umccr/sash/pull/39) for `release/0.7.0`).
3. IF zero or more than one open PR matches, THEN THE PublisherFunction SHALL raise an error
   rather than posting to an ambiguous or wrong PR.
4. THE PublisherFunction SHALL post a comment to the resolved PR, containing the outcome,
   per-status pair counts, critical items (if present), and a link to the results in S3.
5. WHEN the GitHub API call returns a non-2xx HTTP status, THE PublisherFunction SHALL raise an
   error, causing the Lambda to fail and trigger EventBridge's at-least-once retry.
6. A Slack delivery failure SHALL NOT prevent the GitHub post from being attempted, and vice
   versa — both SHALL be attempted independently within the same invocation.

**Confirm with Florian before shipping (not before implementation — the convention above is
verified from git history and safe to build against):**

1. Is `release/<version>` guaranteed for every future release?
2. Can the comparison finish after the PR is already merged/closed? If so, requirement 3's
   "zero matches" case will fire on every such run — decide then whether to fall back to a
   merge-commit comment or skip GitHub and rely on Slack only.

---

### Requirement 4: CDK Infrastructure

**User Story:** As a developer, I want the CDK stack to declare the Publisher Lambda with correct
IAM roles and an EventBridge rule, so the service is fully deployable without manual resource
creation.

#### Acceptance Criteria

1. THE DeploymentStack SHALL define a PublisherFunction Lambda with a dedicated IAM role.
2. THE DeploymentStack SHALL define an EventBridge rule on OrcaBusMain triggering
   PublisherFunction on `SashRegressionComparisonCompleted` events from source
   `sash-regression.comparator`.
3. THE PublisherRole SHALL grant `secretsmanager:GetSecretValue` scoped to exactly the Slack
   webhook secret ARN and the GitHub token secret ARN — no wildcard on all secrets.
4. THE ComparatorRole SHALL grant `events:PutEvents` on OrcaBusMain.
5. THE DeploymentStack SHALL supply environment variables `SLACK_WEBHOOK_SECRET_ID` and
   `GITHUB_TOKEN_SECRET_ID` to the PublisherFunction, and `EVENTS_BUS_NAME` to the
   ComparatorFunction.

---

### Requirement 5: Unit Tests

**User Story:** As a developer, I want unit tests for the new event emission and both publishing
paths, so correctness is verified without running the full AWS integration.

#### Acceptance Criteria

1. THE test suite SHALL extend `test_comparator_handler.py` to cover: ExecId generated once and
   shared across pairs, `run_summary.json` uploaded with correct content and key, and
   `_emit_completed_event` called with the expected `Detail`.
2. THE test suite SHALL include `test_publisher_slack.py` covering message formatting for each
   of the four outcome values and `post_to_slack` raising on non-2xx response.
3. THE test suite SHALL include `test_publisher_github.py` covering comment formatting and
   `post_to_github` raising on non-2xx response.
4. WHEN all unit tests are executed, THE test suite SHALL pass with no failures.

---

## Out of Scope (this phase)

- Per-pair fan-out (`PairComparatorFunction` + `AggregatorFunction`) — no driving requirement
  yet; today's suite is one pair (SEQC-II-medium). Revisit as its own phase if the suite grows.
- Comparison-engine module refactor (`comprehensive_sash_comparison.py` split).
- Security hardening (Submitter token TTL, `secrets.token_hex`, S3 path-traversal fix, WRU
  error-body checks) — tracked as an independent fix, unrelated to Publisher scope.
- `cdk.out/` git-hygiene fix — unrelated to Publisher scope, can be done independently at any
  time.
