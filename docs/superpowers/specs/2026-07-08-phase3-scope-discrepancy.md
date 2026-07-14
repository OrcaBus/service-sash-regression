# Phase 3 scope discrepancy — reconciled

Date: 2026-07-08
Status: **resolved 2026-07-13** — see [Resolution](#resolution-2026-07-13) below

## What happened

Two designs now exist for "Phase 3 (Publisher/Notifier)" and they disagree significantly:

1. **`docs/superpowers/specs/2026-07-08-comparison-completion-notification-design.md`** —
   the notification-only slice brainstormed and approved in this session (event + Slack
   notifier, shared `exec_id`, run-level `run_summary.json`).
2. **`.kiro/specs/sash-regression-completion/design.md`** — expanded (apparently in the
   IDE, outside this session) into a full "service completion" rewrite: adds
   `PairComparatorFunction` + `AggregatorFunction` for per-pair fan-out, a hand-rolled S3
   conditional-write state machine, a 7-module comparison-engine refactor, and security
   hardening (token TTL, `secrets.token_hex`, S3 path-traversal fix, WRU error-body
   checks).

## Why they disagree with the actual plan

Cross-checked against the Obsidian planning notes (source of truth for phase scope):

- **`Daily/2026-07-11.md`** — the already-written Phase 3 task spec:
  - Goal: **Publisher Lambda — post comparison results to GitHub + Slack** (not Slack only)
  - Event contract: `{newVersion, baselineVersion, portalRunId, outcome: PASS|FAIL|MINOR_DIFF, resultS3Prefix, metricSummary}`
  - Comparator change: emit event at end of `run_logging.py`
  - IAM: `sash-regression/github-token` secret + SSM Slack webhook param
  - **Rough size: 3–4 days**
  - Out of scope: auto-blocking sash release PRs, trend dashboards, ctTSO shared lib
- **`OrcaBus/Sash Regression Service.md`** — Phase 3 ("Publisher") was explicitly deferred
  until Phase 2 (Submitter/Watcher) was working, and scoped narrowly as "Post result as
  GitHub PR comment or Slack message." No mention anywhere of per-pair fan-out,
  Aggregator, or a parser refactor as part of Phase 3.
- **`_Brain/Best Practices.md`** — "Scope discipline": _"Only do exactly what was asked...
  do not infer or add logical follow-on steps unless explicitly asked... when unsure
  whether something is in scope, ask rather than assume."_

Neither existing design doc (mine or the `.kiro` rewrite) includes GitHub posting or
`portalRunId`, both of which are explicit in the actual Phase 3 plan. The `.kiro` rewrite
additionally multiplies the estimated 3–4 day Publisher-only phase into a multi-week
architecture overhaul that was never scoped with Florian the way Phases 1–2 were.

## Open items to resolve later

- [ ] Reconcile event contract field names: `outcome`/`portalRunId`/`metricSummary` (per
      the Daily note plan) vs `status`/`jobId`/`criticalItems` (per the `.kiro` rewrite) vs
      `status`/`resultS3Uri` (per my superpowers design)
- [ ] Add GitHub posting to whichever design is chosen — currently missing from both
      technical designs, present only in the Obsidian plan
- [ ] Decide whether `PairComparatorFunction` + `AggregatorFunction` fan-out is in scope for
      _this_ phase or a separate, later phase — Phase 1/2 notes only ever describe a single
      test-case pair (SEQC-II-medium); fan-out for "up to ~10 pairs" appears nowhere in the
      phase plan
- [ ] Decide whether the security hardening items (token TTL, path traversal,
      `secrets.token_hex`, WRU error-body) ship alongside Phase 3 or as their own
      independent fix, given they're unrelated to "Publisher" in scope
- [ ] Write `OrcaBus/Sash Regression Service - Phase 3.md` (still an open task on
      `Daily/2026-07-11.md`) once the above is settled, so the Obsidian spec and the repo
      design doc agree before implementation starts

No changes made to `.kiro/specs/sash-regression-completion/design.md` as part of this note
— it's left as-is pending a decision.

---

## Resolution (2026-07-13)

Decision: **Phase 3 stays Publisher-only**, matching the Obsidian plan and the 3-4 day estimate.

- **Scope**: `.kiro/specs/sash-regression-completion/design.md` and `requirements.md` rewritten
  to Publisher-only (Comparator emits `SashRegressionComparisonCompleted`, new
  `PublisherFunction` posts to GitHub _and_ Slack). The fan-out (`PairComparatorFunction` +
  `AggregatorFunction`), the comparison-engine refactor, and the `cdk.out/` git-hygiene fix are
  dropped from this phase entirely — no driving requirement exists yet (today's suite is one
  pair), and none of it was scoped with Florian. Revisit as a separate phase if the test suite
  grows.
- **Event contract**: merged — Obsidian's `{newVersion, baselineVersion, portalRunId, outcome,
resultS3Prefix, metricSummary}` is the base (non-negotiable per the Daily note), extended with
  per-status pair counts and critical/warning items inside `metricSummary` (useful for Slack/
  GitHub message formatting, doesn't conflict with the base fields). Superseded: the `.kiro`
  rewrite's `status`/`jobId`/`criticalItems`-only shape, and the original superpowers draft's
  `status`/`resultS3Uri` shape.
- **GitHub posting**: added — new `PublisherFunction` posts to both GitHub and Slack,
  independently (one failing must not block the other). Originally designed as a PR comment,
  which needed a PR-lookup mechanism; that lookup was resolved from git history
  (`release/<version>` head-branch convention holds across every sash release, 0.6.0–0.7.0, no
  exceptions) but still needed Florian's confirmation on two edge cases before shipping. **Later
  simplified further (2026-07-13, same day)**: switched from a PR comment to posting a **new
  GitHub issue** per run instead. This removes the PR-lookup step and its race condition
  entirely — every run creates an issue regardless of release-branch/PR state — so there's no
  longer anything to confirm with Florian on this piece.
- **Security hardening** (token TTL, `secrets.token_hex`, S3 path-traversal fix, WRU error-body
  checks): filed as an independent fix, not bundled into Phase 3. Draft issue:
  `docs/superpowers/specs/2026-07-13-security-hardening-issue-draft.md` (not yet posted to
  GitHub — awaiting review).
- Obsidian doc written: [[Sash Regression Service - Phase 3]], now the source of truth alongside
  the rewritten `.kiro` spec — the two agree as of this resolution.
- The removed fan-out/Aggregator/parser-refactor design content is not discarded — full detail
  preserved in `docs/superpowers/specs/2026-07-13-phase4-fanout-backlog.md` for whenever that
  becomes real scope.

All four open items above are resolved; no outstanding items remain on this note.
