"""Final holdout evaluator: binding, stage order, one-time semantics (offline, synthetic).

No real July-September 2026 outcome is used: a synthetic artefact is built with a synthetic
"holdout" start H = 2025-07-01 on the synthetic design of ``tests/test_phase6.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.config import (
    ConfigError,
    EvaluationWindowSpec,
    FinalArtefactSpec,
    FinalEvaluationProtocol,
    FinalInputsSpec,
    HighDemandSpec,
    load_final_evaluation_protocol,
)
from nem_forecast.evaluation import holdout as ho
from nem_forecast.evaluation.backtest import SLICE_COLUMNS, TARGET
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap
from nem_forecast.evaluation.final_artefact import build_final_artefact, load_final_artefact
from nem_forecast.evaluation.folds import HoldoutAccessError
from nem_forecast.evaluation.phase6 import CONFIG_FILES
from nem_forecast.provenance import sha256_file, write_json_atomic
from tests.conftest import REPO_ROOT
from tests.test_phase6 import ALLOW, BCFG, design, small_proto  # noqa: F401 - fixture reuse

H = dt.date(2025, 7, 1)
LAST = dt.date(2025, 7, 31)
PROTOCOL = load_final_evaluation_protocol()
RECORD = PROTOCOL.artefact.candidate_record
TAGS = {
    t: {"tag_object": f"{i}" * 40, "commit": f"{i + 4}" * 40}
    for i, t in enumerate(("data-contract-v1", "feature-contract-v1", "lightgbm-development-v1"))
}
CODE = {"commit": "c" * 40, "dirty": False}


def fake_git(*args: str) -> str | None:
    ref = args[-1]
    tag = ref.removesuffix("^{commit}")
    if tag not in TAGS:
        return None
    return TAGS[tag]["commit" if ref.endswith("^{commit}") else "tag_object"]


@pytest.fixture(scope="module")
def world(
    design: pd.DataFrame,  # noqa: F811
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[dict[str, Any]]:
    base = tmp_path_factory.mktemp("holdout")
    feats = base / "features"
    feats.mkdir()
    design.drop(columns=["origin_date"]).to_parquet(feats / "part.parquet", index=False)
    (base / "feature_manifest.json").write_text('{"status": "complete"}\n')
    files = (("features/part.parquet", sha256_file(feats / "part.parquet")),)
    manifest_sha = sha256_file(base / "feature_manifest.json")
    provenance = {
        "code": {"commit": "a" * 40, "dirty": False},
        "tags": TAGS,
        "inputs": {
            "feature_run_id": "synthetic_features",
            "feature_run_manifest_sha256": manifest_sha,
            "feature_outputs_verified": [{"path": p, "sha256": s} for p, s in files],
        },
        "config_hashes": {f: sha256_file(REPO_ROOT / f) for f in CONFIG_FILES},
        "benchmark_record_hashes": {RECORD.path: RECORD.sha256},
    }
    art_dir = base / "runs" / "synthetic_artefact"
    pre = design[design["origin_date"] < H]
    m = build_final_artefact(pre, art_dir, small_proto(), BCFG, provenance, holdout_first=H)
    training = design[design["origin_date"] <= H - dt.timedelta(days=2)]
    y = training[TARGET].where(training["target_available"]).dropna()
    window = EvaluationWindowSpec(
        first_origin=H, last_origin=LAST, expected_origins=31, expected_rows=744
    )
    spec = FinalArtefactSpec(
        run_id="synthetic_artefact",
        model_producing_commit="a" * 40,
        pre_evaluation_manifest_sha256=sha256_file(art_dir / "pre_evaluation_manifest.json"),
        candidate_record=RECORD,
        models={
            n: {
                "file": i["file"],
                "alpha": i["alpha"],
                "iterations": i["iterations"],
                "sha256": i["sha256"],
            }
            for n, i in m["models"].items()
        },
        trailing_offsets={
            "file": "trailing_offsets.parquet",
            "rows": 72,
            "sha256": sha256_file(art_dir / "trailing_offsets.parquet"),
        },
        hybrid_fallback={
            "file": "hybrid_fallback.json",
            "weight": m["hybrid_fallback"]["weight"],
            "sha256": m["hybrid_fallback"]["sha256"],
        },
        feature_allowlist_sha256=ho.allowlist_sha256(ALLOW),
        lightgbm_version=m["environment"]["lightgbm"],
        contract_tags=TAGS,
    )
    protocol = PROTOCOL.model_copy(
        update={
            "artefact": spec,
            "inputs": FinalInputsSpec(
                feature_run_id="synthetic_features",
                feature_run_manifest_sha256=manifest_sha,
                profile="primary_operational",
                files=tuple({"path": p, "sha256": s} for p, s in files),
            ),
            "holdout": window,
            "rehearsal": EvaluationWindowSpec(
                first_origin=dt.date(2025, 5, 1),
                last_origin=dt.date(2025, 5, 31),
                expected_origins=31,
                expected_rows=744,
            ),
            "slices": PROTOCOL.slices.model_copy(
                update={
                    "high_demand": HighDemandSpec(
                        rule="training_quantile",
                        training_last_origin=H - dt.timedelta(days=2),
                        training_rows=len(y),
                        threshold_mw=float(np.quantile(y, 0.9)),
                    ),
                    "expected_seasons": ("winter",),
                }
            ),
        }
    )
    source = ho.FeatureSource(base, files, "synthetic", ("feature_manifest.json", manifest_sha))
    record = base / "threshold_record.json"
    write_threshold_record(record, protocol)
    with pytest.MonkeyPatch.context() as mp:  # pin the synthetic provenance record
        mp.setattr(ho, "THRESHOLD_RECORD", str(record))
        mp.setattr(ho, "THRESHOLD_RECORD_SHA256", sha256_file(record))
        yield {
            "base": base,
            "art": art_dir,
            "protocol": protocol,
            "source": source,
            "design": design,
            "record": record,
        }


def write_threshold_record(path: Path, p: FinalEvaluationProtocol, **override: Any) -> None:
    hd = p.slices.high_demand
    record = {
        "protocol_id": p.protocol_id,
        "rule": hd.rule,
        "quantile": 0.9,
        "threshold_mw": hd.threshold_mw,
        "training_rows": hd.training_rows,
        "training_last_origin": hd.training_last_origin.isoformat(),
        "feature_run_id": p.inputs.feature_run_id,
        "feature_run_manifest_sha256": p.inputs.feature_run_manifest_sha256,
        **override,
    }
    path.write_text(json.dumps(record, indent=2) + "\n")


def make_spec(
    w: dict[str, Any], tmp: Path, run_id: str = "syn_final", **kw: Any
) -> ho.EvaluationSpec:
    p: FinalEvaluationProtocol = w["protocol"]
    fields: dict[str, Any] = {
        "mode": "final_holdout",
        "run_id": run_id,
        "artefact_dir": w["art"],
        "output_dir": tmp / "runs" / run_id,
        "ledger_path": tmp / "ledger.json",
        "window": p.holdout,
        "features": w["source"],
    }
    fields.update(kw)
    return ho.EvaluationSpec(**fields)


def run(w: dict[str, Any], spec: ho.EvaluationSpec, **kw: Any) -> dict[str, Any]:
    protocol = kw.pop("protocol", w["protocol"])
    return ho.run_evaluation(
        spec,
        protocol,
        BCFG,
        kw.pop("allowlist", ALLOW),
        REPO_ROOT,
        kw.pop("code", CODE),
        fake_git,
        **kw,
    )


@pytest.fixture(scope="module")
def done(world: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("done")
    spec = make_spec(world, tmp)
    pre = ho.preflight(spec, world["protocol"], BCFG, ALLOW, REPO_ROOT, CODE, fake_git)
    write_json_atomic(tmp / "preflight.json", pre)
    return {"spec": spec, "manifest": run(world, spec), "tmp": tmp}


# --- protocol -------------------------------------------------------------------------


REAL_THRESHOLD_RECORD = (
    "docs/benchmarks/final_evaluation_high_demand_threshold.json",
    "b39b14a640e86a0f631137fe2f774c64e6afa087b6e7ca0c6503fc14f42f586c",
)


def test_real_threshold_is_frozen_scalar_with_verified_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ho, "THRESHOLD_RECORD", REAL_THRESHOLD_RECORD[0])
    monkeypatch.setattr(ho, "THRESHOLD_RECORD_SHA256", REAL_THRESHOLD_RECORD[1])
    got = ho.verify_threshold(PROTOCOL, REPO_ROOT, BCFG.slices.high_demand_training_quantile)
    assert got["threshold_mw"] == 6243.067666666667 == PROTOCOL.slices.high_demand.threshold_mw
    assert all(got["verification"].values()) and got["historical_target_rows_opened"] == 0
    assert PROTOCOL.slices.high_demand.training_rows == 39384


def test_real_protocol_freezes_window_and_identities() -> None:
    p = PROTOCOL
    assert p.rehearsal.expected_rows == 2208 and p.rehearsal.expected_origins == 92
    assert (p.holdout.first_origin, p.holdout.last_origin) == (
        dt.date(2026, 7, 1),
        dt.date(2026, 9, 30),
    )
    assert (p.holdout.expected_origins, p.holdout.expected_rows, p.holdout.horizons_per_origin) == (
        92,
        2208,
        24,
    )
    assert len(ho.window_origins(p.holdout)) == 92
    assert p.headline_models == ("lightgbm_trailing_calibrated", "day_week_hybrid_frozen")
    assert p.artefact.pre_evaluation_manifest_sha256.startswith("58ef2cfb7048")
    assert p.artefact.model_producing_commit.startswith("bfed6d7")
    assert p.artefact.feature_allowlist_sha256 == ho.allowlist_sha256(ALLOW)
    assert p.rehearsal.last_origin < p.holdout.first_origin
    assert p.slices.high_demand.training_last_origin == dt.date(2026, 6, 29)
    assert p.quantile_order == "frozen_uncorrected" and p.decision_rule == "descriptive_final"


def test_protocol_validator_rejects_changes(tmp_path: Path) -> None:
    import yaml

    data = yaml.safe_load((REPO_ROOT / "configs" / "final_evaluation.yaml").read_text())
    for path, value in (
        (("holdout", "expected_rows"), 2207),
        (("holdout", "last_origin"), "2026-09-29"),
        (("headline_models",), ["lightgbm_raw", "day_week_hybrid_frozen"]),
        (("bootstrap", "replicates"), 1000),
        (("rehearsal", "last_origin"), "2026-07-31"),
    ):
        bad = json.loads(json.dumps(data, default=str))
        node = bad
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        root = tmp_path / "_".join(path)
        (root / "configs").mkdir(parents=True)
        (root / "pyproject.toml").write_text("[project]\nname='x'\n")
        (root / "configs" / "final_evaluation.yaml").write_text(yaml.safe_dump(bad))
        from nem_forecast.paths import ProjectPaths

        with pytest.raises(ConfigError):
            load_final_evaluation_protocol(paths=ProjectPaths(root))
    with pytest.raises(ValueError):
        EvaluationWindowSpec(
            first_origin=H, last_origin=LAST, expected_origins=30, expected_rows=720
        )


# --- end to end -------------------------------------------------------------------------


def test_end_to_end_stage_order_and_instrumentation(done: dict[str, Any]) -> None:
    m, out = done["manifest"], done["spec"].output_dir
    inst = m["instrumentation"]
    assert m["status"] == "complete" and m["pristine_first_evaluation"] is True
    assert inst["prediction_rows_generated"] == 2 * 744
    assert inst["max_origin_predicted"] == "2025-07-31"
    assert inst["prediction_file_sha256"] == sha256_file(out / "predictions.parquet")
    assert inst["predictions_sealed_utc"] < inst["targets_first_opened_utc"]
    assert inst["target_rows_loaded"] == 744
    assert inst["identical_row_reconciliation"]["identical_rows"] == 744
    assert inst["holdout_evaluation_executed"] is True
    assert m["stages"] == {
        "predictions_generated": True,
        "predictions_sealed": True,
        "targets_opened": True,
        "metrics_calculated": True,
    }
    assert m["model_producing_commit"] == "a" * 40 and m["evaluation_code"] == CODE
    assert not (out / "predictions.parquet").stat().st_mode & 0o222  # read-only
    seal = json.loads((out / "prediction_seal.json").read_text())
    assert seal["prediction_file_sha256"] == inst["prediction_file_sha256"]
    for name in (
        "pre_execution_manifest.json",
        "metrics.json",
        "metrics.md",
        "evaluation_rows.parquet",
        "run_manifest.json",
    ):
        assert (out / name).is_file()
    ledger = json.loads(done["spec"].ledger_path.read_text())
    assert ledger["status"] == "complete" and ledger["completed_run_id"] == "syn_final"


def test_preflight_record_matches_run(done: dict[str, Any]) -> None:
    pre_run = done["spec"].output_dir / "pre_execution_manifest.json"
    assert sha256_file(done["tmp"] / "preflight.json") == sha256_file(pre_run)
    rec = json.loads(pre_run.read_text())
    assert rec["evaluation_target_rows_loaded"] == 0 and rec["evaluation_feature_rows_loaded"] == 0
    assert rec["command"].endswith("--artefact-run-id synthetic_artefact --run-id syn_final")


def test_repeat_and_overwrite_refused(world: dict[str, Any], done: dict[str, Any]) -> None:
    spec = done["spec"]
    with pytest.raises(FileExistsError):
        run(world, spec)
    again = replace(spec, run_id="syn_again", output_dir=done["tmp"] / "runs" / "syn_again")
    with pytest.raises(ho.EvaluationRefusedError, match="already exists"):
        run(world, again)
    assert not again.output_dir.exists()


# --- binding --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "check"),
    [
        ("models/lightgbm_p50.txt", "model_p50"),
        ("trailing_offsets.parquet", "trailing_offsets"),
        ("hybrid_fallback.json", "hybrid_fallback"),
    ],
)
def test_tampered_artefact_refused(
    world: dict[str, Any], tmp_path: Path, target: str, check: str
) -> None:
    copy = tmp_path / "art"
    shutil.copytree(world["art"], copy)
    f = copy / target
    f.chmod(0o644)
    f.write_bytes(f.read_bytes() + b"\n")
    spec = make_spec(world, tmp_path, artefact_dir=copy)
    with pytest.raises(ho.EvaluationRefusedError, match=check):
        run(world, spec)
    failure = json.loads((spec.output_dir / "failure_manifest.json").read_text())
    assert failure["predictions_generated"] is False and failure["targets_opened"] is False
    assert failure["retry_allowed_after_review"] is True
    assert not (spec.output_dir / "predictions.parquet").exists()


def test_wrong_pinned_hashes_and_allowlist_refused(world: dict[str, Any], tmp_path: Path) -> None:
    p: FinalEvaluationProtocol = world["protocol"]
    wrong = p.model_copy(
        update={
            "artefact": p.artefact.model_copy(update={"pre_evaluation_manifest_sha256": "0" * 64})
        }
    )
    with pytest.raises(ho.EvaluationRefusedError, match="pre_evaluation_manifest_sha256"):
        ho.verify_artefact(world["art"], wrong, ALLOW, REPO_ROOT, fake_git)
    with pytest.raises(ho.EvaluationRefusedError, match="feature_allowlist_order"):
        ho.verify_artefact(world["art"], p, tuple(reversed(ALLOW)), REPO_ROOT, fake_git)
    moved = lambda *a: "f" * 40  # noqa: E731 - a moved tag
    with pytest.raises(ho.EvaluationRefusedError, match="contract_tags_in_repository"):
        ho.verify_artefact(world["art"], p, ALLOW, REPO_ROOT, moved)
    ok = ho.verify_artefact(world["art"], p, ALLOW, REPO_ROOT, fake_git)
    assert all(ok["checks"].values()) and ok["model_producing_commit"] == "a" * 40


def test_dirty_tree_and_rehearsal_window_refused(world: dict[str, Any], tmp_path: Path) -> None:
    spec = make_spec(world, tmp_path)
    with pytest.raises(ho.EvaluationRefusedError, match="clean"):
        run(world, spec, code={"commit": "c" * 40, "dirty": True})
    assert not spec.output_dir.exists()
    bad = make_spec(world, tmp_path, mode="rehearsal")  # window = the (synthetic) holdout
    with pytest.raises(HoldoutAccessError):
        run(world, bad)


# --- stage separation -------------------------------------------------------------------


def test_prediction_projection_excludes_targets(world: dict[str, Any]) -> None:
    p: FinalEvaluationProtocol = world["protocol"]
    cols = ho.prediction_columns(p, ALLOW)
    assert not {TARGET, "target_available"} & set(cols)
    assert not [c for c in cols if "eligible" in c or "missing_reason" in c]
    leaky = p.model_copy(
        update={
            "stages": p.stages.model_copy(
                update={"prediction_columns_extra": ("lag_168h__available", "target_available")}
            )
        }
    )
    with pytest.raises(ho.StageOrderError):
        ho.prediction_columns(leaky, ALLOW)


def test_targets_read_only_after_seal(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = make_spec(world, tmp_path)
    calls: list[tuple[list[str], bool, Any, Any]] = []
    original = ho.FeatureSource.read

    def spy(self: ho.FeatureSource, columns: Any, first: Any, last: Any) -> pd.DataFrame:
        sealed = (spec.output_dir / "prediction_seal.json").exists()
        calls.append((list(columns), sealed, first, last))
        return original(self, columns, first, last)

    monkeypatch.setattr(ho.FeatureSource, "read", spy)
    m = run(world, spec)
    w = world["protocol"].holdout
    assert len(calls) == 2  # one feature read, one target read; no pre-window target read
    (fcols, fsealed, _, _), (tcols, tsealed, tfirst, tlast) = calls
    assert TARGET not in fcols and not fsealed
    assert TARGET in tcols and tsealed and (tfirst, tlast) == (w.first_origin, w.last_origin)
    inst = m["instrumentation"]
    assert inst["target_rows_loaded"] == w.expected_rows
    assert inst["pre_window_training_target_rows_loaded"] == 0


def test_open_targets_requires_valid_seal(world: dict[str, Any], tmp_path: Path) -> None:
    log = ho.StageLog(tmp_path)
    opened: list[str] = []
    with pytest.raises(ho.StageOrderError):
        ho.open_targets(log, world["source"], world["protocol"].holdout, opened.append)
    assert opened == [] and log.targets_opened is False
    window = world["protocol"].holdout
    preds = world["source"].read(["origin_market"], window.first_origin, window.first_origin)
    preds = preds.assign(model="a")
    ho.seal_predictions(preds, log)
    (tmp_path / "predictions.parquet").chmod(0o644)
    pd.DataFrame({"model": ["b"]}).to_parquet(tmp_path / "predictions.parquet")
    with pytest.raises(ho.StageOrderError, match="changed"):
        ho.open_targets(log, world["source"], world["protocol"].holdout, opened.append)
    assert opened == []


def test_row_contract(world: dict[str, Any]) -> None:
    window = world["protocol"].holdout
    rows = world["source"].read(["origin_utc", "horizon"], window.first_origin, window.last_origin)
    ho.check_rows(rows, window, "ok")
    for broken, what in (
        (rows.iloc[1:], "missing"),
        (pd.concat([rows, rows.iloc[:1]]), "duplicate"),
        (pd.concat([rows.iloc[1:], rows.iloc[:1].assign(horizon=25)]), "extra"),
    ):
        with pytest.raises(ho.RowContractError, match=what):
            ho.check_rows(broken, window, "t")


def test_no_fitting_tuning_or_calibration(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lightgbm as lgb

    from nem_forecast.evaluation import calibration, phase6
    from nem_forecast.models import baselines, lightgbm_quantile

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("fitting/tuning/calibration called during evaluation")

    monkeypatch.setattr(lgb, "train", boom)
    monkeypatch.setattr(lightgbm_quantile, "fit_quantile", boom)
    monkeypatch.setattr(phase6, "select_structure", boom)
    monkeypatch.setattr(phase6, "_fit_quantiles", boom)
    monkeypatch.setattr(phase6, "residual_offsets", boom)
    monkeypatch.setattr(calibration, "fit_residual_offsets", boom)
    monkeypatch.setattr(baselines.DayWeekHybrid, "fit", boom)
    assert run(world, make_spec(world, tmp_path))["status"] == "complete"


# --- predictions ------------------------------------------------------------------------


def test_prediction_formulas_and_exact_reload(world: dict[str, Any], done: dict[str, Any]) -> None:
    preds = pd.read_parquet(done["spec"].output_dir / "predictions.parquet")
    art = load_final_artefact(world["art"])
    p: FinalEvaluationProtocol = world["protocol"]
    w = p.holdout
    inputs = world["source"].read(ho.prediction_columns(p, ALLOW), w.first_origin, w.last_origin)
    pd.testing.assert_frame_equal(preds, ho.predict_rows(art, inputs, p), check_exact=True)
    cand = preds[preds["model"] == p.selected_candidate].reset_index(drop=True)
    for n, q in ho.QUANTILE_OF.items():
        off = art.offsets[art.offsets["quantile"] == q].set_index("horizon")["offset"]
        assert (cand[n] == cand[f"raw_{n}"] + cand["horizon"].map(off)).all()
    bench = preds[preds["model"] == p.benchmark].reset_index(drop=True)
    rows = inputs.sort_values(ho.KEYS, kind="mergesort").reset_index(drop=True)
    wgt = art.hybrid_weight
    expected = np.where(
        rows["horizon"] == 24,
        rows["lag_168h"],
        wgt * rows["lag_24h"] + (1 - wgt) * rows["lag_168h"],
    )
    assert np.array_equal(bench["raw_p50"].to_numpy(), expected)
    hyb = art.hybrid_offsets.set_index("horizon")
    for n in ho.QNAMES:
        assert (bench[n] == bench["raw_p50"] + bench["horizon"].map(hyb[f"offset_q{n[1:]}"])).all()
    assert bench.loc[bench["horizon"] == 24, "fallback_used"].all()


# --- metrics --------------------------------------------------------------------------


def test_summary_tail_misses_and_frozen_metric_set() -> None:
    frame = pd.DataFrame(
        {
            "p10": [0.0, 0.0, 0.0, 0.0],
            "p50": [5.0, 5.0, 5.0, 5.0],
            "p90": [10.0] * 4,
            "actual": [-1.0, 5.0, 11.0, 12.0],
            "available": True,
            "is_civil_evening": [True, False, False, True],
            "is_high_demand": False,
        }
    )
    s = ho.summary(frame)
    assert s["lower_tail_miss_rate"] == 0.25 and s["upper_tail_miss_rate"] == 0.5
    assert s["coverage_p10_p90"] == 0.25 and s["mae_mw"] == pytest.approx(4.75)
    from nem_forecast.config import FINAL_REPORTED_METRICS

    assert set(FINAL_REPORTED_METRICS) <= set(s)


def test_slices_and_daily_block_bootstrap(world: dict[str, Any], done: dict[str, Any]) -> None:
    res = json.loads((done["spec"].output_dir / "metrics.json").read_text())
    p: FinalEvaluationProtocol = world["protocol"]
    assert set(res["slices_identical_rows"]) == set(p.slices.dimensions)
    hours = {r["horizon"] for r in res["slices_identical_rows"]["horizon"]}
    assert hours == set(range(1, 25))
    assert res["high_demand"]["threshold_mw"] == p.slices.high_demand.threshold_mw
    ev = pd.read_parquet(done["spec"].output_dir / "evaluation_rows.parquet")
    ev["origin_date"] = ev["origin_market"].dt.date
    a = ev[ev["model"] == p.selected_candidate].set_index(ho.KEYS).sort_index()
    b = ev[ev["model"] == p.benchmark].set_index(ho.KEYS).sort_index()
    again = paired_block_bootstrap(
        a["p50"] - a["actual"], b["p50"] - b["actual"], a["origin_date"], 2000, 20261016, 0.95
    )
    mae = res["paired_bootstrap_identical_rows"][0]
    assert mae["metric"] == "mae_mw" and mae["dates"] == 31 and mae["rows"] == 744
    assert mae["mae_difference_mw"] == pytest.approx(again["mae_difference_mw"], abs=1e-9)
    assert mae["ci_low"] == pytest.approx(again["ci_low"], abs=1e-9)
    head = res["headline_identical_rows"][p.selected_candidate]
    assert head["mae_mw"] == pytest.approx(float(np.mean(np.abs(a["p50"] - a["actual"]))))
    assert res["paired_bootstrap_identical_rows"][1]["metric"] == "mean_pinball"


def test_crossings_reported_not_sorted(world: dict[str, Any], done: dict[str, Any]) -> None:
    p: FinalEvaluationProtocol = world["protocol"]
    preds = pd.read_parquet(done["spec"].output_dir / "predictions.parquet")
    cand = preds["model"] == p.selected_candidate
    idx = preds.index[cand][:5]
    preds.loc[idx, "p10"] = preds.loc[idx, "p50"] + 50.0  # inject crossings
    w = p.holdout
    cols = list(dict.fromkeys([*ho.KEYS, "origin_market", *ho.TARGET_COLUMNS, *SLICE_COLUMNS]))
    targets = world["source"].read(cols, w.first_origin, w.last_origin)
    ev, res = ho.evaluate(preds, targets, 6000.0, BCFG, p, w)
    natural = int(
        (
            (preds.loc[cand, "p10"] > preds.loc[cand, "p50"])
            | (preds.loc[cand, "p50"] > preds.loc[cand, "p90"])
        ).sum()
    )
    crossing = res["quantile_crossing_frozen_order"][p.selected_candidate]["calibrated"]
    assert crossing["rows_crossed"] == natural >= 5
    kept = ev[(ev["model"] == p.selected_candidate)]
    assert int((kept["p10"] > kept["p50"]).sum()) >= 5  # values kept in frozen order


# --- failure semantics ------------------------------------------------------------------


def test_failure_before_target_access_allows_pristine_retry(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*a: Any, **k: Any) -> None:
        raise RuntimeError("synthetic prediction failure")

    spec = make_spec(world, tmp_path, run_id="first")
    with monkeypatch.context() as mp:
        mp.setattr(ho, "predict_rows", broken)
        with pytest.raises(RuntimeError, match="synthetic"):
            run(world, spec)
    f = json.loads((spec.output_dir / "failure_manifest.json").read_text())
    assert (
        f["predictions_generated"],
        f["predictions_sealed"],
        f["targets_opened"],
        f["metrics_calculated"],
    ) == (False, False, False, False)
    assert f["retry_allowed_after_review"] and f["pristine_first_evaluation_still_possible"]
    retry = run(world, make_spec(world, tmp_path, run_id="second"))
    assert retry["pristine_first_evaluation"] is True


def test_failure_after_target_access_is_disclosed(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*a: Any, **k: Any) -> None:
        raise RuntimeError("synthetic metric failure")

    spec = make_spec(world, tmp_path, run_id="first")
    with monkeypatch.context() as mp:
        mp.setattr(ho, "evaluate", broken)
        with pytest.raises(RuntimeError, match="synthetic"):
            run(world, spec)
    f = json.loads((spec.output_dir / "failure_manifest.json").read_text())
    assert (
        f["predictions_generated"],
        f["predictions_sealed"],
        f["targets_opened"],
        f["metrics_calculated"],
    ) == (True, True, True, False)
    assert not f["retry_allowed_after_review"] and "pristine" in f["disclosure"]
    ledger = json.loads(spec.ledger_path.read_text())
    assert ledger["attempts"][0]["targets_opened"] is True
    with pytest.raises(ho.EvaluationRefusedError, match="acknowledge-prior-target-access"):
        run(world, make_spec(world, tmp_path, run_id="second"))
    third = run(
        world, make_spec(world, tmp_path, run_id="third", acknowledge_prior_target_access=True)
    )
    assert third["pristine_first_evaluation"] is False


def test_threshold_is_the_frozen_scalar(world: dict[str, Any], done: dict[str, Any]) -> None:
    frozen = world["protocol"].slices.high_demand.threshold_mw
    out = done["spec"].output_dir
    res = json.loads((out / "metrics.json").read_text())
    assert (
        res["high_demand"]["threshold_mw"] == frozen and res["high_demand"]["recomputed"] is False
    )
    assert res["high_demand"]["historical_target_rows_opened"] == 0
    assert all(res["high_demand"]["verification"].values())
    ev = pd.read_parquet(out / "evaluation_rows.parquet")
    assert (ev["high_demand_threshold_mw"] == frozen).all()
    assert (ev["is_high_demand"] == (ev["actual"] >= frozen)).all()
    pre = json.loads((out / "pre_execution_manifest.json").read_text())
    assert pre["high_demand_threshold"]["threshold_mw"] == frozen
    assert pre["high_demand_threshold"]["provenance_record"]["sha256"] == ho.THRESHOLD_RECORD_SHA256
    inst = done["manifest"]["instrumentation"]
    assert inst["high_demand_threshold_mw"] == frozen and inst["high_demand_threshold_verified"]


def test_pre_window_targets_cannot_change_threshold(
    world: dict[str, Any], done: dict[str, Any], tmp_path: Path
) -> None:
    """Poisoned historical targets: identical threshold and high-demand classification."""
    poisoned = world["design"].drop(columns=["origin_date"]).copy()
    pre_window = poisoned["origin_market"].dt.date < H
    poisoned.loc[pre_window, TARGET] = poisoned.loc[pre_window, TARGET] * 10 + 1e6
    (tmp_path / "f").mkdir()
    poisoned.to_parquet(tmp_path / "f" / "part.parquet", index=False)
    src = ho.FeatureSource(
        tmp_path, (("f/part.parquet", sha256_file(tmp_path / "f" / "part.parquet")),), "poisoned"
    )
    spec = make_spec(world, tmp_path, features=src)
    assert run(world, spec)["instrumentation"]["pre_window_training_target_rows_loaded"] == 0
    a = pd.read_parquet(done["spec"].output_dir / "evaluation_rows.parquet")
    b = pd.read_parquet(spec.output_dir / "evaluation_rows.parquet")
    pd.testing.assert_series_equal(a["is_high_demand"], b["is_high_demand"])
    pd.testing.assert_series_equal(a["high_demand_threshold_mw"], b["high_demand_threshold_mw"])


def test_altered_threshold_or_provenance_refused(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p: FinalEvaluationProtocol = world["protocol"]
    hd = p.slices.high_demand
    altered = p.model_copy(
        update={
            "slices": p.slices.model_copy(
                update={
                    "high_demand": hd.model_copy(update={"threshold_mw": hd.threshold_mw + 1e-6})
                }
            )
        }
    )
    spec = make_spec(world, tmp_path, run_id="altered_value")
    with pytest.raises(ho.EvaluationRefusedError, match="threshold_mw"):
        run(world, spec, protocol=altered)
    f = json.loads((spec.output_dir / "failure_manifest.json").read_text())
    assert f["predictions_generated"] is False and f["targets_opened"] is False
    with pytest.raises(ho.EvaluationRefusedError, match="threshold"):
        ho.preflight(
            make_spec(world, tmp_path, run_id="pf"), altered, BCFG, ALLOW, REPO_ROOT, CODE, fake_git
        )
    # tampered record bytes (pinned hash no longer matches)
    tampered = tmp_path / "tampered.json"
    tampered.write_text(world["record"].read_text() + " ")
    monkeypatch.setattr(ho, "THRESHOLD_RECORD", str(tampered))
    with pytest.raises(ho.EvaluationRefusedError, match="record_sha256"):
        run(world, make_spec(world, tmp_path, run_id="tampered"))
    # a re-pinned record with another provenance identity
    other = tmp_path / "other.json"
    write_threshold_record(other, p, protocol_id="another-protocol")
    monkeypatch.setattr(ho, "THRESHOLD_RECORD", str(other))
    monkeypatch.setattr(ho, "THRESHOLD_RECORD_SHA256", sha256_file(other))
    with pytest.raises(ho.EvaluationRefusedError, match="protocol_id"):
        run(world, make_spec(world, tmp_path, run_id="other"))


def test_preflight_reads_no_rows(world: dict[str, Any], tmp_path: Path) -> None:
    spec = make_spec(world, tmp_path, run_id="pf_only")
    disabled = replace(spec.features, reading_disabled=True)
    with pytest.raises(ho.StageOrderError, match="disabled"):
        disabled.read(["origin_utc"], H, LAST)
    pre = ho.preflight(spec, world["protocol"], BCFG, ALLOW, REPO_ROOT, CODE, fake_git)
    assert pre["evaluation_target_rows_loaded"] == 0 and not spec.output_dir.exists()


# --- CLI ------------------------------------------------------------------------------


def test_cli_refusals_read_nothing(tmp_project) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    r = runner.invoke(
        app, ["evaluate-holdout", "--artefact-run-id", "final_artefact_20261017", "--run-id", "x"]
    )
    assert r.exit_code == 1 and "HoldoutAccessError" in r.output and "nothing was read" in r.output
    r = runner.invoke(
        app,
        [
            "evaluate-holdout",
            "--owner-approved-final-evaluation",
            "--artefact-run-id",
            "final_artefact_20261017",
        ],
    )
    assert r.exit_code == 1 and "--run-id" in r.output and "nothing was read" in r.output
    r = runner.invoke(
        app,
        [
            "evaluate-holdout",
            "--owner-approved-final-evaluation",
            "--artefact-run-id",
            "some_other_artefact",
            "--run-id",
            "x",
            "--root",
            str(REPO_ROOT),
        ],
    )
    assert r.exit_code == 1 and "not the frozen artefact" in r.output
    assert not (REPO_ROOT / "results" / "runs" / "x").exists()
    r = runner.invoke(
        app,
        [
            "evaluate-holdout",
            "--owner-approved-final-evaluation",
            "--artefact-run-id",
            "final_artefact_20261017",
            "--run-id",
            "x",
            "--root",
            str(tmp_project.root),
        ],
    )
    assert r.exit_code == 1 and "ConfigError" in r.output  # no protocol in the throwaway root
