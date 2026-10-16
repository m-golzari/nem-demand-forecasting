"""Repository-domain hygiene: current tracked text holds NEM project material only.

High-specificity terms from an unrelated project domain are searched in every
tracked filename and text file, with **zero exceptions**:

* each pattern embeds a one-character class (for example ``re[a]ctor``), so the
  pattern source in this file can never match itself;
* matching is case-sensitive with word boundaries where a term could be a
  substring of something else, so hexadecimal digests (e.g. SHA-256 values in
  the snapshot lock) never match structurally. No hash value is allowlisted.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.conftest import REPO_ROOT

TERMS = [
    r"per-re[a]ctor",
    r"[Bb]iore[a]ctor",
    r"\bd[F]BA\b",
    r"\b[F]BA\b",
    r"\bMon[o]d\b",
    r"\bC[H]O\b",
    r"\bE\. ?c[o]li\b",
    r"\bL[a]ya\b",
    r"[Bb]iopro[c]ess",
    r"[Bb]iophar[m]a",
    r"[Ff]ermen[t]ation",
    r"[Cc]ell cul[t]ure",
    r"[Mm]ass[- ]bal[a]nce",
    r"[Ss]toichiome[t]r",
    r"\b[Bb]ioma[s]s\b",
    r"\b[Mm]etaboli[c]\b",
    r"\b[Mm]etabolis[m]\b",
]
PATTERN = re.compile("|".join(TERMS))


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable")
    return [line for line in out.splitlines() if line]


def test_no_domain_specific_terms_in_tracked_text_zero_exceptions() -> None:
    hits = []
    for rel in tracked_files():
        if PATTERN.search(rel):
            hits.append(f"filename: {rel}")
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits += [
            f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1) if PATTERN.search(line)
        ]
    assert hits == []


def test_patterns_match_terms_but_not_their_own_source_or_hashes() -> None:
    for term in (
        "per-" + "re" + "actor",
        "dF" + "BA",
        "Mon" + "od",
        "C" + "HO",
        "E. " + "coli",
        "La" + "ya",
        "bio" + "process",
        "mass" + "-balance",
    ):
        assert PATTERN.search(f"x {term} y"), term
    assert not PATTERN.search(TERMS[0]) and not PATTERN.search("|".join(TERMS))
    digest = "c9b384d926a508d5fcbdbd7c10bbb390833b2a5cdd589" + "dfba" + "74710"
    assert not PATTERN.search(digest)  # word boundaries + case sensitivity, not an allowlist
    assert not PATTERN.search("CHOICE of horizon")
