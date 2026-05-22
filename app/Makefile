.PHONY: install test check fix invoke-local

install:
	pip install -r requirements-dev.txt

test:
	pytest tests/ --cov=comparator --cov-report=term-missing

check:
	ruff check comparator tests

fix:
	ruff check --fix comparator tests
	ruff format comparator tests

# Run comparator end-to-end against real S3 data.
# Requires: AWS_PROFILE=umccr-prod-operator and env vars set.
# Example:
#   TESTDATA_CONFIG_S3_URI=s3://test-data-503977275616-ap-southeast-2/testdata/config/sash-regression/testdata-cases.yaml \
#   RESULT_S3_PREFIX=s3://test-data-503977275616-ap-southeast-2/testdata/analysis/production/sash-regression \
#   make invoke-local
invoke-local:
	python -c "from comparator.lambdas.comparator.handler import handler; import json; print(json.dumps(handler({'new_version':'0.7.0','baseline_version':'0.6.4','case_name':'SEQC-II-medium'}, None), indent=2, default=str))"
