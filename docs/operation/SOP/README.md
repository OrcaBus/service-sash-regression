SOPs — Sash Regression Service
================================================================================

- [Summary](#summary)
- [Manually running a regression comparison](#manually-running-a-regression-comparison)
- [Submitting a new sash version for regression testing](#submitting-a-new-sash-version-for-regression-testing)
- [Deploying a new version of the service](#deploying-a-new-version-of-the-service)
- [Adding a new testdata pair](#adding-a-new-testdata-pair)
- [Troubleshooting](#troubleshooting)


## Summary

This is the index for SOPs for various operational tasks on the Sash Regression Service.

Tasks covered:

- Manually invoking the Comparator Lambda to run a regression comparison
- Submitting a new sash version to OrcaBus for regression testing via the Submitter Lambda
- Deploying a new version of the service itself to beta/prod
- Adding a new tumor/normal testdata pair to the comparison config
- Troubleshooting common failures


<a name="pm.sr.1"></a>
## Manually running a regression comparison

* [PM.SR.1 - Manually Invoking a Regression Comparison](PM.SR.1/PM.SR.1-ManualComparatorInvocation.md)


<a name="pm.sr.2"></a>
## Submitting a new sash version for regression testing

* [PM.SR.2 - Submitting a New Sash Version for Regression Testing](PM.SR.2/PM.SR.2-SubmittingNewSashVersion.md)


<a name="pm.sr.3"></a>
## Deploying a new version of the service

* [PM.SR.3 - Deploying a New Version of the Sash Regression Service](PM.SR.3/PM.SR.3-ServiceDeployment.md)


<a name="pm.sr.4"></a>
## Adding a new testdata pair

* [PM.SR.4 - Adding a New Testdata Pair](PM.SR.4/PM.SR.4-AddingTestdataPair.md)


<a name="pm.sr.5"></a>
## Troubleshooting

* [PM.SR.5 - Troubleshooting Common Issues](PM.SR.5/PM.SR.5-Troubleshooting.md)


---

> **Legacy:** The `SR.1/` directory contains an earlier version of the submission SOP and its `generate-WRU-draft.sh` helper script. It is superseded by PM.SR.2 above but kept for the script.
