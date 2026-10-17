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


BENCHMARK = REPO_ROOT / "docs" / "benchmarks" / "phase4_development_benchmark.json"


def _benchmark() -> dict:
    import json

    return json.loads(BENCHMARK.read_text(encoding="utf-8"))


def test_readme_benchmark_numbers_match_frozen_record() -> None:
    b = _benchmark()
    status = README.split("## Current status", 1)[1].split("## Quickstart", 1)[0]
    boot = b["paired_bootstrap_mae_difference_mw"]
    vs_same = boot["day_week_hybrid-same_weekday_4w"]
    vs_week = boot["day_week_hybrid-previous_week"]
    assert f"about **{-vs_same['mae_difference_mw']:.0f} MW**" in status
    assert f"about **{-vs_week['mae_difference_mw']:.0f} MW**" in status
    assert vs_same["ci_high"] < 0 and vs_week["ci_high"] < 0  # intervals exclude zero
    cal = b["calibration_coverage_p10_p90"]
    assert f"**{100 * cal['summer']:.1f}% in summer**" in status
    assert f"**{100 * cal['fold_7_2026_01']:.1f}% in January" in status
    assert f"**{100 * cal['holiday']:.1f}% on public holidays**" in status
    assert f"weight of {b['hybrid_weight']:.2f} on yesterday" in status
    assert b["final_holdout_evaluated"] is False
    assert b["development_origins"] == ["2025-07-01", "2026-06-30"]
    assert "July 2025 to June 2026" in status


def test_readme_status_rows() -> None:
    status = README.split("## Current status", 1)[1].split("### Data", 1)[0]
    for needle in (
        "tagged `data-contract-v1`",
        "tagged `feature-contract-v1`",
        "Phase 4 development baseline evaluation | ✅ complete; tagged `baseline-benchmark-v1`",
        "LightGBM development evaluation | ✅ complete",
        "not loaded or evaluated",
        "monitoring and dashboard | ⏳ not yet implemented",
    ):
        assert needle in status, needle


def test_tags_named_in_readme_exist_locally_when_git_available() -> None:
    import subprocess

    try:
        tags = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "tag", "-l"], capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return
    for tag in ("data-contract-v1", "feature-contract-v1", "baseline-benchmark-v1"):
        assert tag in README and (tag in tags or not tags)


CANDIDATE = REPO_ROOT / "docs" / "benchmarks" / "phase6_lightgbm_development_candidate.json"


def _candidate() -> dict:
    import json

    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


def test_readme_lightgbm_table_and_claims_match_candidate_record() -> None:
    c, b = _candidate(), _benchmark()
    status = README.split("## Current status", 1)[1].split("## Quickstart", 1)[0]
    cm, bm = c["metrics"], c["benchmark_metrics"]
    for name, m in (("Frozen day/week hybrid", bm), ("LightGBM, trailing-calibrated", cm)):
        row = (
            f"| {name} | {m['mae_mw']:.1f} MW | {100 * m['wape']:.2f}% | "
            f"{m['mean_pinball']:.1f} MW | {100 * m['coverage_p10_p90']:.1f}% |"
        )
        assert row in status, row
    assert bm["mae_mw"] == b["metrics_full_horizon"]["mae_mw"]  # same frozen benchmark
    imp = c["improvement_vs_benchmark"]
    assert f"about **{imp['mae_mw']:.0f} MW, or {imp['mae_percent']:.0f}%**" in status
    assert imp["mae_bootstrap_hybrid_minus_candidate"]["ci_low"] > 0  # interval excludes zero
    lim = c["conditional_calibration_limitations"]
    assert f"**{100 * lim['coverage_high_demand']:.1f}%**" in status
    assert f"**{100 * lim['coverage_fold_11_2026_05']:.1f}%** in May 2026" in status
    assert f"**{100 * lim['coverage_fold_12_2026_06']:.1f}%** in June 2026" in status
    assert c["holdout_boundary"] == {
        "first_origin": "2026-07-01",
        "last_origin": "2026-09-30",
        "holdout_rows_loaded": 0,
        "evaluated": False,
    }
    assert "holdout\n  remains untouched" in status
    assert c["rows_identical"] == 8760 and "same 8,760 hourly forecasts" in status
    assert c["candidate"] == "lightgbm_trailing_calibrated"
