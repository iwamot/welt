"""Checks that the Lambda packaging pins stay in step with the function.

The Makefile reads the function's Runtime, Architectures, and Handler from
`template.yaml`, so the wheels' Python version and the release ZIP's name follow
the function. The wheel platform is still pinned in the Makefile: `sam build`
succeeds with any pin, and a mismatch only shows at cold start, when a C
extension built for the other platform fails to import. These tests are what
keep that pin from drifting apart, and what keep the Lambda Python version in
the CI matrix.
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


def function_property(name: str) -> str:
    value = re.search(rf"^      {name}: (\S+)$", TEMPLATE, re.MULTILINE)
    assert value
    return value.group(1)


def function_python_version() -> str:
    return function_property("Runtime").removeprefix("python")


def function_architecture() -> str:
    return function_property("Architectures").strip("[]")


def test_the_wheels_target_the_function_architecture():
    pinned = re.search(r"--python-platform (\S+)", MAKEFILE)
    assert pinned
    machine = pinned.group(1).split("-")[0]
    assert LAMBDA_ARCHITECTURES[machine] == function_architecture()


def test_ci_runs_the_function_python_version():
    versions = re.search(r"python-versions: '(.+)'", COMPATIBILITY)
    assert versions
    assert function_python_version() in json.loads(versions.group(1))
