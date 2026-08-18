"""Checks that every restatement of the Slack app manifest stays in step.

`manifest.yml` is restated in two places a reader cannot eyeball against it:
the docs link to Slack's app-creation screen with the manifest percent-encoded
into the URL, and `docs/lambda.md` shows the `settings:` section rewritten for
HTTP serving. These tests are what keep both from drifting apart from the file.
A failure names the page and prints the URL to replace the stale one with.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parent.parent
MANIFEST = (ROOT / "manifest.yml").read_text()
LAMBDA_DOC = (ROOT / "docs" / "lambda.md").read_text()
MARKDOWN = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md")])

CREATE_APP_PREFIX = "https://api.slack.com/apps?new_app=1&manifest_yaml="
# Percent-encoding leaves only unreserved characters, so the pattern ends where
# the URL does instead of running into the surrounding Markdown.
CREATE_APP_URL = re.compile(re.escape(CREATE_APP_PREFIX) + r"[A-Za-z0-9_.~%-]+")
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def create_app_url(manifest: str) -> str:
    return CREATE_APP_PREFIX + quote(manifest, safe="")


def linked_urls() -> list[tuple[Path, str]]:
    return [
        (path, url)
        for path in MARKDOWN
        for url in CREATE_APP_URL.findall(path.read_text())
    ]


def as_socket_mode(settings: str) -> str:
    """Undo the edits that turn the manifest's `settings:` into the HTTP one."""
    kept = [line for line in settings.splitlines() if "request_url:" not in line]
    return "\n".join(kept).replace(
        "socket_mode_enabled: false", "socket_mode_enabled: true"
    )


def test_the_docs_link_to_the_app_creation_screen():
    assert linked_urls()


def test_every_linked_manifest_matches_manifest_yml():
    expected = create_app_url(MANIFEST)
    stale = sorted({path.name for path, url in linked_urls() if url != expected})
    assert not stale, f"Stale links in {', '.join(stale)}. Replace with:\n{expected}"


def test_the_lambda_settings_block_only_adds_http_serving():
    blocks = YAML_BLOCK.findall(LAMBDA_DOC)
    assert len(blocks) == 1
    shown = textwrap.dedent(blocks[0]).rstrip("\n")
    _, _, settings = MANIFEST.rstrip("\n").partition("settings:\n")
    assert as_socket_mode(shown) == "settings:\n" + settings
