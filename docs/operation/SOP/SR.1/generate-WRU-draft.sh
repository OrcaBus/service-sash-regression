#!/usr/bin/env bash

# Set to fail
set -euo pipefail

# Globals
STACK_NAME="SashRegressionStack"
API_URL_OVERRIDE=""

# CLI defaults
FORCE=false
NEW_VERSION=""
BASELINE_VERSION=""
TUMOR_LIBRARY_ID=""
NORMAL_LIBRARY_ID=""

# SOP constants
SOP_VERSION="2026.07.03"
GITHUB_REPO="OrcaBus/service-sash-regression"
THIS_SCRIPT_PATH="docs/operation/generate-WRU-draft.sh"

# Functions
echo_stderr(){
  echo "$(date -Iseconds)" "$@" >&2
}

print_usage(){
  : '
  Print usage help docs
  '
  echo "
generate-WRU-draft.sh [-h | --help]
generate-WRU-draft.sh (tumor_library_id) (normal_library_id)
                      (--new-version <version>)
                      (--baseline-version <version>)
                      [-f | --force]
                      [--api-url <url>]

Description:
Submit a sash regression run (new_version vs baseline_version) for the given tumor/normal
library pair. This posts to this service's deployed Submitter Lambda (via API Gateway),
which looks up the sash workflow, checks OrcaBus for an existing umccr_tested_ run with the
same codeVersion, and — if none exists — pushes a DRAFT WorkflowRunUpdate event via the
WruDraftValidator Lambda. All workflow lookup / idempotency / payload construction happens
server-side; this script is a thin CLI wrapper only.

Positional arguments:
  tumor_library_id:   Tumor library ID (e.g. L2301218).
  normal_library_id:  Normal library ID (e.g. L2301217).

Keyword arguments:
  -h | --help                        Print this help message and exit.
  --new-version <version>            (Required) sash version to test, e.g. 0.7.0.
  --baseline-version <version>       (Required) sash version to compare against, e.g. 0.6.4.
  -f | --force                        (Optional) Don't confirm before submitting.
  --api-url <url>                     (Optional) Override the Submitter API URL. Defaults to
                                       the 'SubmitterApiUrl' CloudFormation output of
                                       ${STACK_NAME} in the current AWS account/region.

Environment:
  AWS_PROFILE:  (Optional) The AWS CLI profile to use for authentication.
  AWS_REGION:   (Optional) The AWS region to use for AWS CLI commands.

Binaries:
  - aws CLI should be installed and configured with appropriate credentials and region.
  - jq should be installed for JSON parsing.
  - curl should be installed for making API requests.

Example usage:
bash generate-WRU-draft.sh L2301218 L2301217 --new-version 0.7.0 --baseline-version 0.6.4
"
}

check_binaries(){
  for binary in aws jq curl; do
    if ! command -v "${binary}" > /dev/null 2>&1; then
      echo_stderr "Error: ${binary} is not installed. Please install ${binary} and try again. Exiting."
      return 1
    fi
  done
}

get_submitter_api_url(){
  : '
  Resolve the Submitter API URL from the SashRegressionStack CloudFormation outputs
  '
  aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --output json \
    --query "Stacks[0].Outputs" | \
  jq --raw-output \
    '
      map(select(.OutputKey == "SubmitterApiUrl")) |
      .[0].OutputValue // empty
    '
}

# Get args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_usage
      exit 0
      ;;
    --new-version)
      NEW_VERSION="$2"
      shift 2
      ;;
    --new-version=*)
      NEW_VERSION="${1#*=}"
      shift
      ;;
    --baseline-version)
      BASELINE_VERSION="$2"
      shift 2
      ;;
    --baseline-version=*)
      BASELINE_VERSION="${1#*=}"
      shift
      ;;
    -f|--force)
      FORCE=true
      shift
      ;;
    --api-url)
      API_URL_OVERRIDE="$2"
      shift 2
      ;;
    --api-url=*)
      API_URL_OVERRIDE="${1#*=}"
      shift
      ;;
    *)
      if [[ -z "${TUMOR_LIBRARY_ID}" ]]; then
        TUMOR_LIBRARY_ID="$1"
      elif [[ -z "${NORMAL_LIBRARY_ID}" ]]; then
        NORMAL_LIBRARY_ID="$1"
      else
        echo_stderr "Error: Unexpected positional argument '$1'. Exiting."
        print_usage
        exit 1
      fi
      shift
      ;;
  esac
done

if ! check_binaries; then
  print_usage
  exit 1
fi

if [[ -z "${TUMOR_LIBRARY_ID}" || -z "${NORMAL_LIBRARY_ID}" ]]; then
  echo_stderr "Error: tumor_library_id and normal_library_id are required. Exiting."
  print_usage
  exit 1
fi

if [[ -z "${NEW_VERSION}" || -z "${BASELINE_VERSION}" ]]; then
  echo_stderr "Error: --new-version and --baseline-version are required. Exiting."
  print_usage
  exit 1
fi

if ! aws sts get-caller-identity --output json > /dev/null 2>&1; then
  echo_stderr "Error: AWS CLI is not configured properly. Please configure your AWS CLI with appropriate credentials and region. Exiting."
  exit 1
fi

api_url="${API_URL_OVERRIDE}"
if [[ -z "${api_url}" ]]; then
  if ! api_url="$(get_submitter_api_url)"; then
    echo_stderr "Error: Failed to look up the ${STACK_NAME} CloudFormation stack. Ensure you're logged into the correct AWS account."
    exit 1
  fi
fi
if [[ -z "${api_url}" ]]; then
  echo_stderr "Error: Could not resolve the Submitter API URL. Pass it explicitly with --api-url."
  exit 1
fi

payload="$( \
  jq --null-input --compact-output --raw-output \
    --arg tumorLibraryId "${TUMOR_LIBRARY_ID}" \
    --arg normalLibraryId "${NORMAL_LIBRARY_ID}" \
    --arg newVersion "${NEW_VERSION}" \
    --arg baselineVersion "${BASELINE_VERSION}" \
    '
      {
        "tumor_library_id": $tumorLibraryId,
        "normal_library_id": $normalLibraryId,
        "new_version": $newVersion,
        "baseline_version": $baselineVersion
      }
    ' \
)"

echo_stderr "Submitting the following payload to ${api_url}:"
jq --raw-output <<< "${payload}" 1>&2
if [[ "${FORCE}" == "false" ]]; then
  read -r -p 'Confirm to submit this sash regression run? (y/n): ' confirm_submit
  if [[ ! "${confirm_submit}" =~ ^[Yy]$ ]]; then
    echo_stderr "Aborting submission."
    exit 1
  fi
fi

response="$( \
  curl --silent --fail-with-body --location --show-error \
    --request POST \
    --header "Content-Type: application/json" \
    --data "${payload}" \
    --url "${api_url}" \
)"

echo_stderr "Submitter response:"
jq --raw-output <<< "${response}"
