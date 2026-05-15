.PHONY: install test check fix

install:
	pip install -r requirements-dev.txt

test:
	pytest tests/ --cov=hello_world --cov-report=term-missing

check:
	ruff check hello_world tests

fix:
	ruff check --fix hello_world tests
	ruff format hello_world tests
