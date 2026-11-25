#!/bin/bash
set -e

# mise
eval "$(mise activate bash)"
mise fmt
mise install

# Python
uv sync --locked
mise run licenses python
uv audit
uv-override-prune --fix
ruff check --fix
ruff format
ty check --error-on-warning
if [[ -n "$CI" ]]; then
  uv run pytest --cov --cov-report=term --cov-report=xml
else
  uv run pytest --cov --cov-report=term
fi

# Shared lint tasks
mise run gha-lint
mise run shell-lint
mise run docker-lint

# AWS SAM
SAM_CLI_TELEMETRY=0 sam validate --lint --template-file template.yaml
# The Makefile fails here if template.yaml stops naming what the manifest needs.
make -s lambda-manifest | python3 -m json.tool >/dev/null
# Build the release ZIP, so a locked dependency without a wheel for the
# function's runtime and architecture fails here instead of at release time.
trap 'rm -rf dist' EXIT
rm -rf dist
make -s lambda-zip

# Check for uncommitted changes
git diff --exit-code
