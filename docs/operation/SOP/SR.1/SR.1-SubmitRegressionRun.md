# Submit a Sash Regression Run

- Version: 1.0
- Contact: Quentin Clayssen, [quentin.clayssen@unimelb.edu.au](mailto:quentin.clayssen@unimelb.edu.au)

Table of Contents

- [Introduction](#introduction)
- [Requirements](#requirements)
- [Procedure](#procedure)
- [Confirmation](#confirmation)

## Introduction

This service compares `sash` pipeline outputs between a new version and a baseline version, to catch regressions before a release. Here we describe the SOP for manually submitting a regression run for a tumor/normal library pair.

A run is submitted by pushing a DRAFT `WorkflowRunUpdate` event to OrcaBus. The Submitter Lambda looks up the `sash` workflow, checks OrcaBus for an existing `umccr_tested_` run with the same `codeVersion`, and, if none exists, pushes the DRAFT event. When that run reaches `SUCCEEDED`, the Comparator compares its outputs against the baseline.

## Requirements

- appropriate AWS permissions
- AWS credentials set up in the local environment
- tools installed
  - AWS CLI
  - JQ
  - curl

## Procedure

To initiate a regression run we submit an initial DRAFT event. For more details consult the main [README](../../../../README.md).
For convenience we provide a shell script that generates and submits the event.

- familiarise yourself with the script: [generate-WRU-draft.sh](./generate-WRU-draft.sh)
  - especially check the settings in the `Globals` section
    - ensure the values are fit for your use case, e.g. for clinical samples match the accredited pipeline details
  - and you understand how to set the input parameters through the CLI, use the `--help` flag if needed
- execute the script, passing the tumor and normal library IDs and the two versions to compare:
  ```
  bash generate-WRU-draft.sh L2301218 L2301217 --new-version 0.7.0 --baseline-version 0.6.4
  ```
  - Note: AWS credentials need to be set on the environment
  - the script resolves the Submitter API URL from the `SubmitterApiUrl` output of the `SashRegressionStack` CloudFormation stack, or you can override it with `--api-url`
- the script prints the JSON payload it is about to submit. Check it reflects the intended request
  - confirm at the prompt to submit (or pass `-f` / `--force` to skip the confirmation)
- the script submits the request and prints the Submitter response
  - take note of the returned `action` and `workflowRunName` / `portalRunId`

## Confirmation

The OrcaBus [Portal](https://portal.umccr.org/) can be used to check whether the event resulted in a WorkflowRun record.

- navigate to the Portal's WorkflowRun listing: https://portal.umccr.org/runs/workflow
- search for your WorkflowRun using the `workflowRunName` or `portalRunId`
- confirm that the WorkflowRun is listed and progressing as expected (check over time)
- once the WorkflowRun has `SUCCEEDED`, the Watcher triggers the Comparator, and the comparison results are uploaded to the results bucket
