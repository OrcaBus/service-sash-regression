# Handover — Sash Regression Service

Written 2026-08-07, handing over from Quentin Clayssen. This is the entry point for taking the
service over. It is deliberately self-contained: everything you need is either here or in this
repo, with nothing depending on a personal Obsidian vault or a chat history.

Read this, then [`README.md`](../README.md) for the service description, then
[`docs/operation/SOP/`](operation/SOP/README.md) for the runbooks.

---

## 1. What this service is, and why it exists

Comparing two `sash` versions by hand is expensive and it blocks the release chain. Someone has
to remember to do it, pull both output trees locally, run a comparison script, and share the
result. Because it is manual, it does not reliably happen, so a `sash` release can change clinical
outputs without anyone noticing until later.

This service automates that: given a new `sash` version and a baseline version, it runs both
against a known testdata pair and produces a stored, reviewable diff of their outputs.

Tracking issue: [umccr/research-projects#232](https://github.com/umccr/research-projects/issues/232).

## 2. Architecture as deployed

Three ARM64 Docker Lambdas, all built from the same `./app` image with different entrypoints.

```
  POST (SOP SR.1 script)
         │
         ▼
  ┌──────────────┐   submits sash run    ┌────────────────────┐
  │  Submitter   │──────────────────────>│  WruDraftValidator │
  │              │                       │  (external Lambda) │
  └──────┬───────┘                       └─────────┬──────────┘
         │ emits                                   │ runs sash as
         │ SashRegressionRunSubmitted              │ umccr_tested_sash_...
         ▼                                         ▼
  (nothing consumes it yet)              ┌────────────────────┐
                                         │  OrcaBus workflow  │
                                         │      manager       │
                                         └─────────┬──────────┘
                                                   │ WorkflowRunStateChange
                                                   │ (name prefix umccr_tested_)
                                                   ▼
                                         ┌────────────────────┐
                                         │      Watcher       │
                                         └─────────┬──────────┘
                                                   │ async invoke on SUCCEEDED
                                                   ▼
                                         ┌────────────────────┐
                                         │     Comparator     │──> results to S3
                                         └────────────────────┘
```

The naming convention `umccr_tested_sash_{new}_vs_{baseline}_{portal_run_id}` is load-bearing:
it is how the Watcher's EventBridge rule recognises our runs, and how the Watcher recovers the
two versions being compared without a database lookup.

Deeper detail: [`.planning/codebase/ARCHITECTURE.md`](../.planning/codebase/ARCHITECTURE.md) and
[`.planning/codebase/STRUCTURE.md`](../.planning/codebase/STRUCTURE.md).

### Buckets

| Bucket                                  | Purpose                              | Access                      |
| --------------------------------------- | ------------------------------------ | --------------------------- |
| `pipeline-*-cache-*`, `project-data-*`  | source `sash` outputs being compared | read-only                   |
| `test-data-503977275616-ap-southeast-2` | curated baseline config/data         | **read-only — never write** |
| `umccr-research-dev`                    | comparison results, every stage      | write                       |

Results always land in `umccr-research-dev` regardless of stage. Promoting a result into the
testdata baseline is a deliberate, manual, one-way admin action.

## 3. What is deployed, and what is not

**Deployed: BETA only.**

|               |                                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------------- |
| Stack         | `SashRegressionStack`                                                                                               |
| Stack ARN     | `arn:aws:cloudformation:ap-southeast-2:843407916570:stack/SashRegressionStack/45ffc080-57fc-11f1-84c7-0ae9e1f279eb` |
| Region        | `ap-southeast-2`                                                                                                    |
| Profile       | `umccr-dev-pu`                                                                                                      |
| Submitter API | `https://sb40dhxr0e.execute-api.ap-southeast-2.amazonaws.com/prod/`                                                 |
| Last deployed | 2026-07-09                                                                                                          |

```sh
aws sso login --profile umccr-dev-pu
cd infrastructure/
AWS_PROFILE=umccr-dev-pu pnpm cdk deploy -c deployMode=beta
```

The `-c deployMode=beta|gamma|prod|stateless|stateful` context flag is **required** — without it
the deploy fails with "deployMode is required" (`bin/deploy.ts`).

**Not deployed: GAMMA and PROD.** They are not deployable as-is — see open item 2.

**Not built: Phase 3 (Publisher).** Designed only.

## 4. How to operate it

Five runbooks in [`docs/operation/SOP/`](operation/SOP/README.md), indexed in that directory's
README. Do not duplicate them here; they are the source of truth for operations.

| SOP     | Task                                                     |
| ------- | -------------------------------------------------------- |
| PM.SR.1 | Manually invoke the Comparator against two existing runs |
| PM.SR.2 | Submit a new sash version for regression testing         |
| PM.SR.3 | Deploy the service to beta/prod                          |
| PM.SR.4 | Add a new tumor/normal testdata pair                     |
| PM.SR.5 | Troubleshooting                                          |

Two gotchas that have cost time before and are easy to hit:

- `generate-WRU-draft.sh` has an interactive `y/n` confirmation. Pass `--force` for
  non-interactive use.
- `AWS_DEFAULT_REGION` must be set, or the SOP script exits 1 with an unhelpful error.

## 5. Reproducing a comparison from scratch

Reconstructed 2026-07-14 from session notes; confirmed working end-to-end 2026-05-26. This is the
most valuable single section here — it is the only complete record of how the first real
comparison was produced.

### What this covers

The Comparator only, invoked **locally** via `make invoke-local`, which calls the handler function
directly in Python. It does not invoke the deployed Lambda and does not exercise the container
image. Nothing auto-triggers: the two `sash` runs being compared must already exist and be staged.

### 1. Prerequisites — the two sash runs

The Comparator does not run `sash`. It compares two **already-completed** runs. For the 2026-05-26
confirmation these were produced on 2026-05-21 via manual SOPS triggers on prod:

- **Sample:** SEQC-II medium (SBJ00480 / HCC1395) — tumor `L2301218`, normal `L2301217`
- **DRAGEN inputs reused:** prod run `20250903331e44aa` (DRAGEN 4.4.4)
- **OA inputs reused:** prod run `202509047dfc99f4` (oncoanalyser-wgts-dna 2.2.0)
- **sash 0.6.4 baseline (`run1`):** portal_run_id `202605212fa0b7ec`
  `s3://pipeline-prod-cache-503977275616-ap-southeast-2/byob-icav2/production/analysis/sash/202605212fa0b7ec/L2301218__L2301217/`
- **sash 0.7.0 comparison (`run2`):** portal_run_id `2026052194996946`
  `s3://pipeline-prod-cache-503977275616-ap-southeast-2/byob-icav2/production/analysis/sash/2026052194996946/L2301218__L2301217/`

Triggered with `generate-WRU-draft-latest.sh` (fetched fresh from the
`service-sash-pipeline-manager` SOP `PM.SH.1`), with `--input-data` pointing explicitly at the
three prod S3 input dirs above, under `AWS_PROFILE=umccr-prod-operator` with `PORTAL_TOKEN`
exported. **Explicit `--input-data` was required** — sash's pipeline-manager auto-populate would
otherwise pick up DRAGEN paths from the wrong ICA project context (`project-wgs-accreditation`).

### 2. Stage the run outputs

The Comparator, as configured for this run, reads from
`s3://umccr-research-dev/quentin/sash-regression/testdata/run{1,2}/L2301218__L2301217/`, **not**
directly from `pipeline-prod-cache`. Sync both run directories there first:

```sh
aws s3 sync \
  s3://pipeline-prod-cache-503977275616-ap-southeast-2/byob-icav2/production/analysis/sash/202605212fa0b7ec/L2301218__L2301217/ \
  s3://umccr-research-dev/quentin/sash-regression/testdata/run1/L2301218__L2301217/ \
  --profile umccr-prod-operator

aws s3 sync \
  s3://pipeline-prod-cache-503977275616-ap-southeast-2/byob-icav2/production/analysis/sash/2026052194996946/L2301218__L2301217/ \
  s3://umccr-research-dev/quentin/sash-regression/testdata/run2/L2301218__L2301217/ \
  --profile umccr-prod-operator
```

`umccr-prod-operator` can read prod cache but writing to `umccr-research-dev` is cross-account —
this sync may need an intermediate local copy. Confirm what your profile can actually do before
assuming it works in one hop.

### 3. Upload the case config

```sh
aws s3 cp config/testdata-cases.yaml \
  s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
  --profile umccr-prod-operator
```

Content used for the 0.7.0-vs-0.6.4 SEQC-II-medium run:

```yaml
alias_run1: 'sash 0.6.4'
alias_run2: 'sash 0.7.0'

pairs:
  - tumor: L2301218
    normal: L2301217
    run1: s3://umccr-research-dev/quentin/sash-regression/testdata/run1/L2301218__L2301217/
    run2: s3://umccr-research-dev/quentin/sash-regression/testdata/run2/L2301218__L2301217/
    metadata:
      subject: SBJ00480
      case: SEQC-II-medium
      cohort: SEQC-II
      run1_portal_run_id: 202605212fa0b7ec
      run2_portal_run_id: 2026052194996946
```

### 4. Invoke the Comparator locally

From `app/`:

```sh
aws sso login --profile umccr-dev-pu
cd app && make invoke-local
```

Which expands to (see `app/Makefile`):

```sh
TESTDATA_CONFIG_S3_URI=s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
RESULT_S3_PREFIX=s3://umccr-research-dev/quentin/sash-regression/results \
AWS_PROFILE=umccr-dev-pu \
python -c "from comparator.lambdas.comparator.handler import handler; import json; \
  print(json.dumps(handler({'new_version':'0.7.0','baseline_version':'0.6.4','case_name':'SEQC-II-medium'}, None), indent=2, default=str))"
```

Note the profile switch: `umccr-dev-pu` here, `umccr-prod-operator` for the staging steps above.
All env vars are set inline by the Makefile target — no `.env` needed. The venv lives at
`app/.venv`; run `make install` from `app/` if it is not set up.

### 5. Known-good reference result

```
s3://umccr-research-dev/quentin/sash-regression/results/0.7.0-vs-0.6.4/SEQC-II-medium/20260526T053829Z/test/data/
```

The timestamp segment is generated per invocation — expect a new one on re-run.

### Gotchas

- The testdata bucket `test-data-503977275616-ap-southeast-2` is **read-only at every stage**.
  Never point `RESULT_S3_PREFIX` at it.
- `make invoke-local` runs the handler in-process. It is for fast iteration, not deploy validation.
- **PCGR tier comparison between 0.6.4 and 0.7.0 is not meaningful** — the column was renamed
  `TIER` → `ACTIONABILITY_TIER` and the refdata changed (20220203 → 20250314). The Comparator does
  not reconcile this. Expect it as a diff, not a bug.
- If you re-trigger fresh `sash` runs rather than reusing the two portal run IDs above, remember
  sash ≥0.6.1 requires OA ≥2.2.0 output structure. Do not mix with OA 2.1.0 inputs.

## 6. Open items

| #   | Item                                                                                              | Detail                                                                                                                                                                                                                                                                                                                                                       | Owner             |
| --- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- |
| 1   | **Watcher never confirmed end-to-end**                                                            | Deployed and wired, but never observed firing on a real `umccr_tested_*` SUCCEEDED event. The 2026-07-09 test submission (`portal_run_id 202607126fb50f26`) stalled because the WRU validator rejected stale Filemanager cache paths — not a defect in the Watcher. It should exercise itself on the next natural test run; until then treat it as unproven. | successor         |
| 2   | **GAMMA/PROD `WruDraftValidator` name unknown**                                                   | `infrastructure/stage/constants.ts` hardcodes the BETA function name `OrcaBusBeta-WruValidatorS-WruDraftValidatorCE0E33B-qPMdDh7awGuX` for all three stages. Blocks any gamma/prod deploy.                                                                                                                                                                   | needs Florian     |
| 3   | **CI is red on `main`**                                                                           | `make check-all` starts with `pnpm audit`, which exits non-zero on 10 high advisories in transitive dev deps (`brace-expansion` via `eslint>minimatch`, `js-yaml`). Every PR fails this job regardless of content. Fix by restoring a `pnpm.overrides` block in `package.json` pinning the patched versions. See §7.                                         | successor         |
| 4   | **Phase 3 (Publisher) not started**                                                               | Designed and reconciled. 14 tasks in `.kiro/specs/sash-regression-completion/tasks.md`, none done. Task 14 is manual Secrets Manager setup (`sash-regression/github-token`, `sash-regression/slack-webhook-*`); no secrets are provisioned yet.                                                                                                              | successor         |
| 5   | **Issue [#8](https://github.com/OrcaBus/service-sash-regression/issues/8) — security hardening**  | Submitter token cache TTL, `secrets.token_hex`, S3 path-traversal, WRU error-body checks. Filed, unstarted. Real bugs, but independent of Phase 3.                                                                                                                                                                                                           | successor         |
| 6   | **Issue [#5](https://github.com/OrcaBus/service-sash-regression/issues/5) — empty DRAFT payload** | Very likely already fixed by merged PR #6 (`cc378f3`, seeds `payload.data` from the prior SUCCEEDED run). Verify against a real submission and close.                                                                                                                                                                                                        | verify then close |
| 7   | `cdk.out/` is tracked in git                                                                      | 37 asset directories committed. Known hygiene issue, deliberately not bundled with anything else. Add to `.gitignore` and `git rm -r --cached`.                                                                                                                                                                                                              | successor         |
| 8   | Phase 4 backlog parked                                                                            | Per-pair fan-out, an Aggregator, and splitting the 3,680-line `comprehensive_sash_comparison.py`. Deliberately deferred — see [`docs/superpowers/specs/2026-07-13-phase4-fanout-backlog.md`](superpowers/specs/2026-07-13-phase4-fanout-backlog.md). Revisit only if the suite grows past ~2-3 pairs.                                                        | not scheduled     |

## 7. Restoring green CI (open item 3)

`pnpm audit` flags high-severity advisories in transitive **dev** dependencies. There is no
runtime exposure — these come in through `eslint` — but `make check` runs `pnpm audit` first and
exits on it, so CI cannot go green without addressing it.

A `pnpm.overrides` block existed in `package.json` previously and was lost during the
`aws-cdk-lib` 2.195 → 2.260 upgrade. Restore it with the currently-required bounds:

```jsonc
"pnpm": {
  "overrides": {
    "brace-expansion@<1.1.18": "^1.1.18",
    "brace-expansion@>=2.0.0 <2.1.4": "^2.1.4",
    "brace-expansion@>=3.0.0 <5.0.9": "^5.0.9",
    "js-yaml@>=3.0.0 <3.15.1": "^3.15.1"
  }
}
```

Then `pnpm install` and re-run `pnpm audit` to confirm. Advisory bounds move — re-derive them from
the live `pnpm audit --json` output rather than trusting the snapshot above.

## 8. Decision log

Decisions that are not obvious from the code, and where the full reasoning lives.

- **The testdata bucket is read-only for this service, at every stage.** It is a curated baseline.
  Results always go to `umccr-research-dev`; promoting a result into the baseline is a manual,
  one-way admin action. This is a hard rule, not a convention.
- **No tolerance band on comparisons.** There used to be a `WARN` status for numeric deltas under
  `0.05`. It was removed 2026-08-07: we could not define what a "relevant" warning means for an
  accreditation bug-fix claim, so any real difference now fails and gets a human look. Full
  rationale: [`docs/comparison-thresholds.md`](comparison-thresholds.md).
- **Phase 3 posts a new GitHub issue, not a PR comment.** A new issue avoids both the PR lookup
  and the "is the PR still open" race. Every run files an issue regardless of release-branch state.
  See [`docs/superpowers/specs/2026-07-08-phase3-scope-discrepancy.md`](superpowers/specs/2026-07-08-phase3-scope-discrepancy.md).
- **Slack via Incoming Webhook, not the `AwsChatBotTopic-alerts` SNS topic.** That topic is
  alarm-only and cross-account. A bot token was rejected as unnecessary scope.
- **Per-pair fan-out was deferred.** A `.kiro` design rewrite had ballooned Phase 3 into a full
  service overhaul that was never scoped with Florian. Today's suite is a single pair, so there is
  no driving requirement. Reconciled back to Publisher-only; the design work is preserved in
  [`docs/superpowers/specs/2026-07-13-phase4-fanout-backlog.md`](superpowers/specs/2026-07-13-phase4-fanout-backlog.md).
- **The Submitter's EventBridge auto-trigger is deferred.** Submission is a manual HTTP POST for
  now, because the generate-draft event this would key off does not exist in OrcaBus yet.

## 9. Who to ask

| Who              | For what                                                                                                                            |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Florian          | Design owner. Phase 2 design was locked with him; he is the person for the GAMMA/PROD WRU validator names and for any scope change. |
| alexiswl         | ICA operations, prod→dev data copies, aborting stuck runs.                                                                          |
| Quentin Clayssen | History and prior context on anything above.                                                                                        |

## 10. Repo map

| Path                                      | What                                                              |
| ----------------------------------------- | ----------------------------------------------------------------- |
| `app/comparator/`                         | comparison engine + Comparator Lambda                             |
| `app/submitter/`                          | OrcaBus submission logic + Submitter Lambda                       |
| `app/watcher/`                            | run tracking + Watcher Lambda                                     |
| `app/tests/`                              | pytest suite (49 tests)                                           |
| `infrastructure/stage/`                   | per-stage CDK: `constants.ts`, `config.ts`, `deployment-stack.ts` |
| `infrastructure/toolchain/`               | CodePipeline stacks (toolchain account)                           |
| `test/`                                   | CDK / cdk-nag compliance tests (needs Docker running)             |
| `config/testdata-cases.yaml`              | the testdata pair config, uploaded to S3 for the Lambda to read   |
| `docs/operation/SOP/`                     | the five operational runbooks                                     |
| `docs/superpowers/specs/`                 | design decisions and parked scope, with dates                     |
| `.kiro/specs/sash-regression-completion/` | Phase 3 requirements, design, and 14-task plan                    |
| `.planning/codebase/`                     | generated architecture/structure/testing notes                    |
