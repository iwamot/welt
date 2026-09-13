"""Checks that the Lambda packaging pins stay in step with the function.

The Makefile resolves wheels for one Python version and one architecture, and
`template.yaml` declares the Runtime and Architectures they are loaded under.
Nothing else ties the two together: `sam build` succeeds either way, and a
mismatch only shows at cold start, when a C extension built for the other
platform fails to import. These tests are what keep the pins from drifting
apart, and what keep the Lambda Python version in the CI matrix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
MAKEFILE = (ROOT / "Makefile").read_text()
TEMPLATE = (ROOT / "template.yaml").read_text()
COMPATIBILITY = (ROOT / ".github" / "workflows" / "compatibility.yml").read_text()

# uv names a platform by its machine first; Lambda calls the same machines
# by its own names.
LAMBDA_ARCHITECTURES = {"aarch64": "arm64", "x86_64": "x86_64"}


def function_python_version() -> str:
    runtime = re.search(r"^\s+Runtime: python(\S+)$", TEMPLATE, re.MULTILINE)
    assert runtime
    return runtime.group(1)


def function_architecture() -> str:
    # A function that leaves Architectures out runs on Lambda's default.
    if "Architectures:" not in TEMPLATE:
        return "x86_64"
    architectures = re.search(r"Architectures: \[(\w+)\]", TEMPLATE)
    assert architectures
    return architectures.group(1)


def test_the_wheels_target_the_function_python_version():
    pinned = re.search(r"--python-version (\S+)", MAKEFILE)
    assert pinned
    assert pinned.group(1) == function_python_version()


def test_the_wheels_target_the_function_architecture():
    pinned = re.search(r"--python-platform (\S+)", MAKEFILE)
    assert pinned
    machine = pinned.group(1).split("-")[0]
    assert LAMBDA_ARCHITECTURES[machine] == function_architecture()


def test_ci_runs_the_function_python_version():
    versions = re.search(r"python-versions: '(.+)'", COMPATIBILITY)
    assert versions
    assert function_python_version() in json.loads(versions.group(1))
