---
inclusion: always
---

# Session Context — Current State & Next Priority

> Last updated: 2026-07-08. Update this file after each significant work session.

## Where We Are

### Git state

- `main` (origin): commit `97c3753` — "feat: gamma deploy mode, codebase mapping, and SR.1 SOP docs (#4)"
- Local `main` matches origin — clean working tree (only `.kiro/steering/context.md` is untracked)
- PR #4 was **squash-merged** into `main` at `97c3753`. The original branch commits `87d5de0` and `82145ad` are not literal ancestors of `main` (squash merge creates a new commit hash), but their combined content is fully present on `main`. Verified by file content: `deployment-stack.ts` has all three roles (ComparatorRole, SubmitterRole, WatcherRole) and `package.json` has `aws-cdk ^2.1129.0`.

> **Do not use `git merge-base --is-ancestor` to check if branch content landed** — squash merges make this unreliable. Always verify by checking actual file content on `main`.

### What is built and deployed (dev/beta)

**Three Lambda functions**, each with its own IAM role:

| Lambda               | Role             | Trigger                                                                                           | Purpose                                                                                       |
| -------------------- | ---------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `ComparatorFunction` | `ComparatorRole` | Direct invoke or Watcher async-invoke                                                             | Downloads sash outputs, schema check, comparison, uploads results                             |
| `SubmitterFunction`  | `SubmitterRole`  | API Gateway POST                                                                                  | Checks OrcaBus for existing run, submits DRAFT via WruDraftValidator, emits EventBridge event |
| `WatcherFunction`    | `WatcherRole`    | EventBridge rule on `orcabus.workflowmanager` / `WorkflowRunStateChange` / `umccr_tested_` prefix | Parses run name, extracts versions, async-invokes Comparator on SUCCEEDED                     |

The three-role split was necessary — a shared role caused a CloudFormation circular dependency because `WatcherRole` needs to reference `ComparatorFunction.functionArn`, which doesn't exist at synth time when the role is shared.

**Watcher design** (no DynamoDB — simpler than original Phase 2 plan):

- EventBridge pattern: `source=orcabus.workflowmanager`, `detailType=WorkflowRunStateChange`, `detail.workflowRunName` prefix `umccr_tested_`
- `parse_run_name()` in `app/watcher/track.py` extracts `new_version` and `baseline_version` using regex: `umccr_tested_sash_{new_slug}_vs_{base_slug}_{portal_run_id}` (portal_run_id is always 16 hex chars)
- On SUCCEEDED: reads `engineParameters.outputUri + outputs.sashRelPath` from the event payload, passes `new_output_path` to Comparator async invoke (`InvocationType="Event"`)
- On FAILED: logs a warning, does nothing else
- No DynamoDB — version info is embedded in `workflowRunName`, no tracking table needed

**Comparator** accepts optional `new_output_path` and `baseline_output_path` overrides in the event payload (added to support Watcher invocation with live S3 paths).

---

## Blocking bug — FIXED 2026-07-08

**Issue #5: Empty `payload.data` — all DRAFT submissions were silently rejected**

Root cause was **not** the WruDraftValidator's JSON schema (`orcabus.workflowmanager@WorkflowRunUpdate` in the `orcabus.events` registry types `payload.data` as a bare `object` with no required sub-fields — `{}` passes that check fine). The validator accepted the event and forwarded it to EventBridge every time.

The real rejection happened one hop downstream, in workflow-manager's `HandleWruEvent` lambda: Django's `Payload.data` model field treats an empty dict as "blank" and `full_clean()` raises `ValidationError: {'data': ['This field cannot be blank.']}` in `workflow_manager/models/base.py`. Confirmed by pulling the live CloudWatch logs for a real `umccr_tested_` submission — the record was built, logged an orcabusId, then failed at `payload.save()` on every retry, and never appeared in workflow-manager's API.

**Fix** (`app/submitter/submit.py`):

- `_find_prior_sash_inputs(tumor, normal)` — looks up the most recent SUCCEEDED sash run (any codeVersion) for the same library pair via `/api/v1/workflowrun` + `/api/v1/payload`, and reuses its `tags`/`inputs` (`dragenSomaticDir`/`dragenGermlineDir`/`oncoanalyserDnaDir` — these don't change between sash versions under regression test, so no need to query dragen-wgts-dna/oncoanalyser-wgts-dna directly). Raises `ValueError` if no prior SUCCEEDED sash run exists for the pair.
- `_build_engine_parameters(portal_run_id, pipeline_id)` — builds fresh `cacheUri`/`logsUri`/`outputUri`/`projectId`/`pipelineId` per run (these must NOT be reused from a prior run, or ICA would write into that prior run's S3 prefix).
- `_build_draft_payload` now takes the resulting `data` dict instead of hardcoding `{}`.

**Verified live**: deployed to dev (`pnpm cdk-beta deploy SashRegressionStack`), invoked the real Submitter Lambda for libraries L2600141/L2600140 → `portalRunId 202607085c1c01e1` now persists in workflow-manager as `status: DRAFT` (previously: never persisted at all). Downstream progression to RUNNING/SUCCEEDED depends on `service-sash-pipeline-manager` picking up the DRAFT — not yet confirmed end-to-end through Watcher → Comparator.

Tests: `app/tests/test_submit.py` covers the new lookup, the engineParameters build, and a `ValueError` case when no prior run exists. 41/41 pytest pass.

---

## Remaining known issues (lower priority)

| Issue                                                                    | File                                             | Impact                                                          |
| ------------------------------------------------------------------------ | ------------------------------------------------ | --------------------------------------------------------------- |
| `wruDraftValidatorFunctionName` identical across BETA/GAMMA/PROD         | `infrastructure/stage/constants.ts`              | Low urgency — only BETA deployed now                            |
| `_find_existing_run` only reads first page of OrcaBus results            | `app/submitter/submit.py` (`_find_existing_run`) | Could submit duplicate runs once many versions exist            |
| `_create_portal_run_id` uses `random.choices` not `secrets.token_hex`    | `app/submitter/submit.py`                        | Minor — not a security context                                  |
| OrcaBus token cache has no TTL — can serve expired tokens on warm Lambda | `app/submitter/submit.py` (`_token_cache`)       | Will cause 401s after token expiry on long-running environments |
| `cdk.out/` committed to git                                              | `.gitignore`                                     | Bloat — stale copies of app source in repo                      |

---

## Key file locations

```
app/submitter/submit.py                              ← BUG: _build_draft_payload data:{}
app/submitter/lambdas/submitter/handler.py           ← Submitter entry point
app/watcher/track.py                                 ← parse_run_name + invoke_comparator
app/watcher/lambdas/watcher/handler.py               ← Watcher entry point
app/comparator/lambdas/comparator/handler.py         ← Comparator entry point
infrastructure/stage/deployment-stack.ts             ← CDK: 3 functions, 3 roles, EventBridge rule
infrastructure/stage/constants.ts                    ← WRU validator name (hardcoded, all stages identical)
docs/operation/SOP/SR.1/generate-WRU-draft.sh        ← CLI wrapper for Submitter API
```

## How to run tests

```sh
cd app && make test
# pytest tests/ --cov=comparator --cov=submitter --cov=watcher --cov-report=term-missing
```

## How to invoke locally

```sh
# Submitter (against deployed API)
bash docs/operation/SOP/SR.1/generate-WRU-draft.sh L2301218 L2301217 \
  --new-version 0.7.0 --baseline-version 0.6.4

# Comparator (local Python, no Docker)
cd app && make invoke-local
```
