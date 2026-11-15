# Packaging for `sam build` (wired via BuildMethod: makefile in template.yaml).
# The artifact holds exactly the Lambda source and the locked dependencies —
# nothing else from the working tree. Dependencies are resolved for the Lambda
# platform, so the build machine's OS and architecture don't leak in; the
# platform pins must match the function's Runtime in template.yaml.
.PHONY: build-WeltFunction lambda-zip

LAMBDA_ZIP := welt-lambda-python3.14-arm64.zip

build-WeltFunction:
	uv export --quiet --frozen --no-dev --no-emit-project -o "$(ARTIFACTS_DIR)/requirements.txt"
	uv pip install -r "$(ARTIFACTS_DIR)/requirements.txt" --target "$(ARTIFACTS_DIR)" \
		--python-platform aarch64-manylinux2014 --python-version 3.14 --only-binary :all:
	rm "$(ARTIFACTS_DIR)/requirements.txt"
	cp -r app lambda_function.py LICENSE "$(ARTIFACTS_DIR)"
	find "$(ARTIFACTS_DIR)" -type d -name __pycache__ -prune -exec rm -rf {} +

lambda-zip:
	mkdir -p dist
	set -eu; \
	build_dir=$$(mktemp -d "$(CURDIR)/dist/.lambda.XXXXXX"); \
	trap 'rm -rf "$$build_dir"' EXIT; \
	mkdir "$$build_dir/package"; \
	$(MAKE) build-WeltFunction ARTIFACTS_DIR="$$build_dir/package"; \
	(cd "$$build_dir/package" && zip -qr "../$(LAMBDA_ZIP)" .); \
	awk -v zip="$(LAMBDA_ZIP)" \
		'$$1 == "CodeUri:" && $$2 == "." && NF == 2 { changed += sub(/[.]$$/, zip) } \
		{ print } END { if (changed != 1) exit 1 }' \
		template.yaml > "$$build_dir/template.yaml"; \
	mv "$$build_dir/$(LAMBDA_ZIP)" "$$build_dir/template.yaml" dist/
