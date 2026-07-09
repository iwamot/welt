# Packaging for `sam build` (wired via BuildMethod: makefile in template.yaml).
# The artifact holds exactly the Lambda source and the locked dependencies —
# nothing else from the working tree. Dependencies are resolved for the Lambda
# platform, so the build machine's OS and architecture don't leak in; the
# platform pins must match the function's Runtime in template.yaml.
build-WeltFunction:
	uv export --frozen --no-dev --no-emit-project -o "$(ARTIFACTS_DIR)/requirements.txt"
	uv pip install -r "$(ARTIFACTS_DIR)/requirements.txt" --target "$(ARTIFACTS_DIR)" \
		--python-platform x86_64-manylinux2014 --python-version 3.14 --only-binary :all:
	rm "$(ARTIFACTS_DIR)/requirements.txt"
	cp -r app lambda_function.py "$(ARTIFACTS_DIR)"
	find "$(ARTIFACTS_DIR)" -type d -name __pycache__ -prune -exec rm -rf {} +
