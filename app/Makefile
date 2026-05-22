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
# Requires: AWS_PROFILE=umccr-prod-operator
# Results written to umccr-research-dev/quentin/ (dev account — NOT testdata bucket)
# Config must be uploaded first:
#   aws s3 cp ../config/testdata-cases.yaml s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml --profile umccr-prod-operator
invoke-local:
	TESTDATA_CONFIG_S3_URI=s3://umccr-research-dev/quentin/sash-regression/config/testdata-cases.yaml \
	RESULT_S3_PREFIX=s3://umccr-research-dev/quentin/sash-regression/results \
	AWS_PROFILE=umccr-prod-operator \
	python -c "from comparator.lambdas.comparator.handler import handler; import json; print(json.dumps(handler({'new_version':'0.7.0','baseline_version':'0.6.4','case_name':'SEQC-II-medium'}, None), indent=2, default=str))"
