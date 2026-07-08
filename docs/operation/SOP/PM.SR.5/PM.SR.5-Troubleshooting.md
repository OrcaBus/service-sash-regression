# Troubleshooting Common Issues

- Version: 2026.07.08
- Contact: Quentin Clayssen

- [Comparator Lambda times out](#comparator-lambda-times-out)
- [Schema check fails](#schema-check-fails)
- [Comparison returns FAIL unexpectedly](#comparison-returns-fail-unexpectedly)
- [Submitter returns HTTP 401 or OrcaBus authentication error](#submitter-returns-http-401-or-orcabus-authentication-error)
- [Submitter Lambda: WruDraftValidator error](#submitter-lambda-wrudraftvalidator-error)
- [Comparator partial results — non-zero exit swallowed](#comparator-partial-results--non-zero-exit-swallowed)
- [CDK tests fail with Docker error](#cdk-tests-fail-with-docker-error)
- [Config not updated after S3 upload](#config-not-updated-after-s3-upload)

## Comparator Lambda times out

**Symptom:** Lambda returns a `Task timed out after 900.00 seconds` error.

**Cause:** Too many pairs in a single invocation, or one pair has very large sash output directories.

**Fix:**

1. Run one pair at a time using `case_name`:
   ```sh
   {"new_version": "0.7.0", "baseline_version": "0.6.4", "case_name": "SEQC-II-medium"}
   ```
2. Check if the sash output directories are unusually large — the Lambda has 10 GiB ephemeral storage and downloads both baseline and new directories to `/tmp` before comparing.
3. Check CloudWatch for the last log line before the timeout to identify which pair or step is slow.

## Schema check fails

**Symptom:** Lambda response contains `"schema": {"passed": false, "missing": ["<file>", ...]}`.

**Cause:** One or more of the 9 required sash output files are missing from the S3 path for that pair.

**Fix:**

1. Confirm the S3 path in `testdata-cases.yaml` is correct and ends with a `/`.
2. List the actual contents of the S3 prefix:
   ```sh
   aws s3 ls s3://<bucket>/<prefix>/ --recursive --profile umccr-dev-pu | awk '{print $4}'
   ```
3. Check if the sash run completed successfully — a failed or partial run may be missing outputs.
4. The schema check hardcodes 9 expected file patterns in `app/comparator/schema_check.py`. If sash renamed an output file in a new version, update `EXPECTED_FILES` in that file.

## Comparison returns FAIL unexpectedly

**Symptom:** `"status": "FAIL"` in the response but you expected a clean run.

**Cause:** File-level differences were detected — could be intentional (version update genuinely changed outputs) or a false positive.

**Fix:**

1. Review `critical_items` in the response to understand what changed.
2. Download and inspect the full results from S3:
   ```sh
   aws s3 sync \
     s3://umccr-research-dev/sash-regression/<new>-vs-<baseline>/<case>/<exec_id>/test/ \
     /tmp/comparison-results/ \
     --profile umccr-dev-pu
   cat /tmp/comparison-results/summary.json | jq .
   ```
3. If the change is intentional (e.g. a new version produces different but correct output), the `FAIL` status is expected — the comparison result is informational for sign-off.

## Submitter returns HTTP 401 or OrcaBus authentication error

**Symptom:** Submitter Lambda logs show an HTTP 401 or `raise_for_status()` error on an OrcaBus API call.

**Cause:** The cached OrcaBus JWT token has expired. The token is cached in memory for the lifetime of the Lambda execution environment (up to 15 minutes) but the token TTL may be shorter.

**Fix:**

1. Force a cold start by deploying a trivial config change to recycle the Lambda execution environment, or wait for the environment to be recycled naturally.
2. The underlying fix is to add TTL-based cache expiry in `app/submitter/submit.py` — see the tech debt in `.planning/codebase/CONCERNS.md`.

## Submitter Lambda: WruDraftValidator error

**Symptom:** Submitter Lambda logs show `RuntimeError: WruDraftValidator error: ...`.

**Cause:** The WruDraftValidator Lambda rejected the DRAFT payload — either the schema is invalid or the function name is wrong for the current stage.

**Fix:**

1. Check the error detail in CloudWatch — the full WRU response body is included in the `RuntimeError` message.
2. Verify `WRU_VALIDATOR_FUNCTION_NAME` in the Lambda environment variables matches the correct function for the stage:
   ```sh
   aws lambda get-function-configuration \
     --function-name <submitter-function-name> \
     --profile umccr-dev-pu \
     --query 'Environment.Variables.WRU_VALIDATOR_LAMBDA_NAME' \
     --output text
   ```
3. If the function name is wrong for beta/prod, update `WRU_VALIDATOR_FUNCTION_NAME` in `infrastructure/stage/constants.ts` and redeploy. See [PM.SR.3](../PM.SR.3/PM.SR.3-ServiceDeployment.md).

## Comparator partial results — non-zero exit swallowed

**Symptom:** Comparison returns a result but the data looks incomplete or truncated. CloudWatch shows a warning like `comparison script exited with non-zero code but output found`.

**Cause:** `comprehensive_sash_comparison.py` crashed partway through but had already written a partial `summary.json`. The comparator treats any existing output as success with a warning.

**Fix:**

1. Check `run.log` in the comparison results S3 path — it contains full stdout/stderr from the comparison script.
2. Look for Python tracebacks or `bcftools` errors in the log.
3. The partial `summary.json` may still be usable if the crash occurred after the main analysis completed. Inspect the file to determine coverage.

## CDK tests fail with Docker error

**Symptom:** `pnpm test` fails with `Cannot connect to the Docker daemon`.

**Cause:** Docker Desktop is not running. CDK synthesizes the Lambda Docker image as part of the test.

**Fix:** Start Docker Desktop, then re-run `pnpm test`.

## Config not updated after S3 upload

**Symptom:** The Comparator Lambda is still using the old config after you uploaded a new `testdata-cases.yaml`.

**Cause:** You uploaded to the wrong S3 path, or the Lambda's `TESTDATA_CONFIG_S3_URI` environment variable points to a different path.

**Fix:**

1. Check the Lambda's configured config URI:
   ```sh
   aws lambda get-function-configuration \
     --function-name <comparator-function-name> \
     --profile umccr-dev-pu \
     --query 'Environment.Variables.TESTDATA_CONFIG_S3_URI' \
     --output text
   ```
2. Verify the file at that exact URI:
   ```sh
   aws s3 cp <TESTDATA_CONFIG_S3_URI> - --profile umccr-dev-pu
   ```
3. Re-upload to the correct path if needed.
