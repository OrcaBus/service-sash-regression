# Draft GitHub issue — security/robustness hardening (Submitter + Comparator)

Not yet posted. Review and edit before filing with `gh issue create --repo OrcaBus/service-sash-regression`.

---

**Title:** Harden Submitter token cache, WRU error handling, and S3 download path safety

**Body:**

Four independent robustness/security fixes identified during Phase 3 design review, unrelated to
the Publisher work in progress. Parked here rather than bundled into that PR. Full context:
`docs/superpowers/specs/2026-07-08-phase3-scope-discrepancy.md`.

### 1. Submitter token cache has no TTL

`app/submitter/submit.py`'s `_orcabus_token()` caches the OrcaBus JWT indefinitely for the life
of the Lambda execution environment (module-level `_token_cache: str | None`). A long-lived warm
container can end up using an expired token.

Fix: cache `(token, fetched_at)` and re-fetch after a TTL (e.g. 600s, comfortably under typical
OrcaBus JWT expiry).

### 2. WRU validator error responses aren't fully checked

`_invoke_wru_validator()` only checks the Lambda invocation's `FunctionError` header and the
response body's `statusCode`. The WRU validator can return HTTP 200 with an `error` field set in
the body — that case currently passes through undetected.

Fix: also inspect `body.get("error")` and raise if non-null.

### 3. Portal run ID uses a non-cryptographic PRNG

`_create_portal_run_id()` uses `random.choices` (Mersenne Twister) for the random suffix.

Fix: use `secrets.token_hex(4)` instead — cheap change, no behavior difference besides RNG
source.

### 4. S3 download path traversal in the Comparator

`app/comparator/s3_utils.py`'s `download_s3_dir()` validates destination paths with a string
`startswith` prefix check, which is bypassable by crafted S3 keys (e.g. sibling-directory tricks
that share a string prefix but aren't actually inside `local_dir`).

Fix:

```python
dest = (local_dir / rel).resolve()
if not dest.is_relative_to(local_dir.resolve()):
    raise ValueError(f"S3 key escapes local_dir: {key!r}")
```

(`Path.is_relative_to()`, Python 3.9+ — immune to string-prefix tricks.) While touching this
function, also switch to a module-level cached `boto3.client("s3")` to avoid per-call credential
resolution overhead.

### Scope note

These are all in already-deployed Lambdas (Submitter, Comparator) — not blocking any in-progress
work, but worth fixing independently since they're real bugs, not just cleanup. Low risk, small
diff; a single PR covering all four is reasonable.
