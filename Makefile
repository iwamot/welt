# Packaging for `sam build` (wired via BuildMethod: makefile in template.yaml).
# The artifact holds exactly the Lambda source and the locked dependencies —
# nothing else from the working tree. Dependencies are resolved for the Lambda
# platform, so the build machine's OS and architecture don't leak in; the
# platform pin must match the function's Architectures in template.yaml.
.PHONY: build-WeltFunction lambda-zip lambda-manifest

# The function in template.yaml is the one place that names its Python version,
# architecture, and handler; the wheels, the ZIP name, and the manifest follow it.
function_property = $(shell sed -n 's/^      $(1): *//p' template.yaml)
LAMBDA_RUNTIME := $(call function_property,Runtime)
LAMBDA_ARCHITECTURE := $(patsubst [%],%,$(call function_property,Architectures))
LAMBDA_HANDLER := $(call function_property,Handler)
PYTHON_VERSION := $(patsubst python%,%,$(LAMBDA_RUNTIME))
LAMBDA_ZIP := welt-lambda-$(LAMBDA_RUNTIME)-$(LAMBDA_ARCHITECTURE).zip

ifeq ($(and $(PYTHON_VERSION),$(LAMBDA_ARCHITECTURE),$(LAMBDA_HANDLER)),)
$(error template.yaml must set Runtime, Architectures, and Handler on the function)
endif

build-WeltFunction:
	uv export --quiet --frozen --no-dev --no-emit-project -o "$(ARTIFACTS_DIR)/requirements.txt"
	uv pip install -r "$(ARTIFACTS_DIR)/requirements.txt" --target "$(ARTIFACTS_DIR)" \
		--python-platform aarch64-manylinux2014 --python-version $(PYTHON_VERSION) --only-binary :all:
	rm "$(ARTIFACTS_DIR)/requirements.txt"
	cp -r app lambda_function.py LICENSE "$(ARTIFACTS_DIR)"
	find "$(ARTIFACTS_DIR)" -type d -name __pycache__ -prune -exec rm -rf {} +

# What a deployment outside SAM needs to know about the ZIP from the same
# release: its name, and the runtime, architecture, and handler it runs under.
lambda-manifest:
	@printf '{\n  "zip": "%s",\n  "runtime": "%s",\n  "architecture": "%s",\n  "handler": "%s"\n}\n' \
		"$(LAMBDA_ZIP)" "$(LAMBDA_RUNTIME)" "$(LAMBDA_ARCHITECTURE)" "$(LAMBDA_HANDLER)"

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
	$(MAKE) --no-print-directory lambda-manifest > "$$build_dir/welt-lambda.json"; \
	mv "$$build_dir/$(LAMBDA_ZIP)" "$$build_dir/template.yaml" "$$build_dir/welt-lambda.json" dist/
