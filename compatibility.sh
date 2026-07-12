#!/bin/bash
set -euo pipefail

# mise (uv only — a full `mise install` would put the pinned Python ahead of
# the version under test)
eval "$(mise activate bash)"
mise install aqua:astral-sh/uv

# CI (compatibility.yml) runs this once per supported Python version — the
# AL2023-based AWS Lambda runtimes, matching the requires-python floor — by
# setting UV_PYTHON. Locally: UV_PYTHON=3.12 ./compatibility.sh
export UV_PROJECT_ENVIRONMENT=".venv-compat/${UV_PYTHON:-default}"

uv sync
# Tests import only the pure-logic modules, so byte-compile the I/O modules
# and entry points too to catch syntax incompatibilities there.
uv run --no-sync python -m compileall -q app main.py lambda_function.py
uv run --no-sync pytest
