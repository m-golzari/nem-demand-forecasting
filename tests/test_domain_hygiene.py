"""Repository-domain hygiene: no text imported from unrelated (bioprocess) projects.

High-specificity terms are checked in every tracked text file and filename.
Matching is case-sensitive with word boundaries, so hexadecimal digests such as
``...589dfba7...`` in the snapshot lock are not matches.

Documented allowlist:
- this test file (it must name the terms);
- the decision-log row ``| D-34 |`` in ``docs/decisions.md``, which records
  the 2026-10-16 correction and must quote the removed wording to be honest.
No other legitimate match exists.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.conftest import REPO_ROOT

TERMS = [
    r"per-reactor",
    r"[Bb]ioreactor",
    r"\bdFBA\b",
    r"\bFBA\b",
    r"\bMonod\b",
    r"\bCHO\b",
    r"\bE\. ?coli\b",
    r"\bLaya\b",
    r"[Bb]ioprocess",
    r"[Bb]iopharma",
    r"[Ff]ermentation",
    r"[Cc]ell culture",
    r"[Mm]ass[- ]balance",
    r"[Ss]toichiometr",
    r"\b[Bb]iomass\b",
    r"\b[Mm]etabolic\b",
    r"\b[Mm]etabolism\b",
]
ALLOWLIST = {"tests/test_domain_hygiene.py"}
ALLOWED_LINE_PREFIXES = {"docs/decisions.md": "| D-34 |"}
PATTERN = re.compile("|".join(TERMS))


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable")
    return [line for line in out.splitlines() if line]


def test_no_unrelated_domain_terms_in_tracked_text() -> None:
    hits = []
    for rel in tracked_files():
        if rel in ALLOWLIST:
            continue
        if PATTERN.search(rel):
            hits.append(f"filename: {rel}")
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            if line.startswith(ALLOWED_LINE_PREFIXES.get(rel, "\0")):
                continue
            if PATTERN.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:80]}")
    assert hits == []


def test_pattern_catches_known_terms_but_not_hex_digests() -> None:
    assert PATTERN.search("Move per-reactor results")
    assert PATTERN.search("dFBA model") and PATTERN.search("Monod kinetics")
    assert not PATTERN.search('"sha256": "c9b384d926a508d5fcbdbd7c10bbb390833b2a5cdd589dfba74710"')
    assert not PATTERN.search("CHOICE of horizon")  # word boundary, case-sensitive


def test_allowlist_is_minimal() -> None:
    text = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    allowed = [line for line in text.splitlines() if PATTERN.search(line)]
    assert len(allowed) == 1 and allowed[0].startswith("| D-34 |")
