"""Documentation-sensitive checks: README paths, links and commands must exist."""

from __future__ import annotations

import re

from nem_forecast.cli import app
from tests.conftest import REPO_ROOT

README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _tree_paths(text: str) -> list[str]:
    block = text.split("## Repository layout", 1)[1].split("```text", 1)[1].split("```", 1)[0]
    stack: list[str] = []
    paths = []
    for line in block.splitlines():
        match = re.search(r"[├└]── ([^\s#]+)", line)
        if not match:
            continue
        depth = match.start() // 4
        stack = [*stack[:depth], match.group(1).rstrip("/")]
        paths.append("/".join(stack))
    return paths


def test_repository_layout_paths_exist() -> None:
    paths = _tree_paths(README)
    assert len(paths) > 20
    missing = [p for p in paths if not (REPO_ROOT / p).exists()]
    assert missing == []


def test_relative_links_exist() -> None:
    links = [m for m in re.findall(r"\]\(([^)#\s]+)\)", README) if "://" not in m]
    assert links
    assert [link for link in links if not (REPO_ROOT / link).exists()] == []


def test_cli_commands_in_readme_exist() -> None:
    registered = {c.name for c in app.registered_commands}
    used = set(re.findall(r"nem-forecast\s+(?:\\\s*)?([a-z][a-z-]+)", README))
    assert used and used <= registered, used - registered


def test_make_targets_in_readme_exist() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z-]+):", makefile, flags=re.MULTILINE))
    used = {
        a or b for a, b in re.findall(r"`make ([a-z-]+)`|^make ([a-z-]+)", README, re.MULTILINE)
    }
    assert used and used <= targets, used - targets
