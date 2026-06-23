.PHONY: test check check-all fix fix-all install build invoke

check:
	@pnpm audit
	@pnpm prettier
	@pnpm lint
	@pre-commit run --all-files

check-all: check
	@(cd app && make check)

fix:
	@pnpm prettier-fix
	@pnpm lint-fix

fix-all: fix
	@(cd app && make fix)

install:
	@pnpm install --frozen-lockfile

test:
	@pnpm test

# Docker targets for local development
IMAGE ?= service-sash-regression-comparator

build:
	docker build -t $(IMAGE) app/

invoke:
	docker run --rm \
		-e AWS_PROFILE=$(AWS_PROFILE) \
		-e TESTDATA_CONFIG_S3_URI=$(TESTDATA_CONFIG_S3_URI) \
		-e RESULT_S3_PREFIX=$(RESULT_S3_PREFIX) \
		-v ~/.aws:/root/.aws:ro \
		$(IMAGE) \
		'{"new_version":"0.7.0","baseline_version":"0.6.4"}'
