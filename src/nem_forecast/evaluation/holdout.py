"""Final holdout evaluation of the frozen artefact (configs/final_evaluation.yaml, D-47).

Two internally enforced stages:

1. **prediction**: verify the artefact, configuration and inputs; read only projected keys
   and approved feature columns for the evaluation origins; generate candidate and
   benchmark predictions; write them atomically and seal them (SHA-256 + seal manifest);
2. **evaluation**: only after the seal, record target access in the ledger, open target
   and slice columns, join on identical keys and compute the frozen metrics, slices and
   paired market-date block bootstrap.

Nothing here fits, tunes or calibrates; the artefact is used as built. Quantiles stay in
their frozen order (crossings are reported, never sorted). A ledger per (protocol,
artefact) refuses a repeat after a successful evaluation and records every attempt that
opened targets. The evaluation is descriptive and final.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from nem_forecast.config import BacktestConfig, EvaluationWindowSpec, FinalEvaluationProtocol
from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.evaluation.backtest import SLICE_COLUMNS, TARGET, attach_evaluation
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap
from nem_forecast.evaluation.final_artefact import FinalArtefact, load_final_artefact
from nem_forecast.evaluation.folds import HoldoutAccessError
from nem_forecast.evaluation.metrics import crossing_magnitude, crossing_rate, summarise
from nem_forecast.models.baselines import DayWeekHybrid, select_inputs
from nem_forecast.provenance import read_json, sha256_file, write_json_atomic

KEYS = ["origin_utc", "horizon"]
PREDICTION_KEYS = (
    "origin_utc",
    "origin_market",
    "horizon",
    "target_hour_utc",
    "availability_profile",
)
TARGET_COLUMNS = (TARGET, "target_available")
QNAMES = ("p10", "p50", "p90")
QUANTILE_OF = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
MODES = ("final_holdout", "rehearsal")
THRESHOLD_TOLERANCE_MW = 1e-9
PROTOCOL_FILE = "configs/final_evaluation.yaml"
FINAL_COMMAND = (
    "nem-forecast evaluate-holdout --owner-approved-final-evaluation "
    "--artefact-run-id {artefact} --run-id {run_id}"
)

GitFn = Callable[..., str | None]


class EvaluationRefusedError(RuntimeError):
    """A precondition failed; raised before any outcome is read."""


class StageOrderError(RuntimeError):
    """Outcome columns would be opened before predictions were sealed (or seal broken)."""


class RowContractError(ValueError):
    """Missing, duplicate or extra (origin, horizon) rows."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def market_midnight_utc(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.combine(day, dt.time(0), tzinfo=NEM_MARKET_TZ)).tz_convert(
        "UTC"
    )


def window_origins(window: EvaluationWindowSpec) -> list[dt.date]:
    return [window.first_origin + dt.timedelta(days=i) for i in range(window.expected_origins)]


def allowlist_sha256(allowlist: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(allowlist)).encode()).hexdigest()


def _py(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


# --- inputs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureSource:
    """Hash-pinned Parquet files holding design rows (features, targets, slice attributes)."""

    root: Path
    files: tuple[tuple[str, str], ...]  # (path relative to root, sha256)
    label: str
    manifest: tuple[str, str] | None = None  # (feature-run manifest path, sha256)

    def verify(self) -> dict[str, Any]:
        bad = [rel for rel, sha in self.files if sha256_file(self.root / rel) != sha]
        if (
            self.manifest is not None
            and sha256_file(self.root / self.manifest[0]) != (self.manifest[1])
        ):
            bad.append(self.manifest[0])
        if bad:
            raise EvaluationRefusedError(f"input hash mismatch: {bad}; nothing was read")
        return {
            "label": self.label,
            "files": [{"path": rel, "sha256": sha} for rel, sha in self.files],
            "manifest": (
                {"path": self.manifest[0], "sha256": self.manifest[1]} if self.manifest else None
            ),
        }

    def read(
        self, columns: Sequence[str] | None, first: dt.date | None, last: dt.date
    ) -> pd.DataFrame:
        """Rows with first <= origin market date <= last, projected to ``columns`` at read."""
        filters: list[tuple[str, str, Any]] = [
            ("origin_utc", "<", market_midnight_utc(last + dt.timedelta(days=1)))
        ]
        if first is not None:
            filters.append(("origin_utc", ">=", market_midnight_utc(first)))
        parts = [
            pd.read_parquet(
                self.root / rel, columns=None if columns is None else list(columns), filters=filters
            )
            for rel, _ in self.files
        ]
        frame = pd.concat(parts, ignore_index=True)
        if columns is not None and list(frame.columns) != list(columns):
            raise StageOrderError(f"read returned unexpected columns {list(frame.columns)}")
        return frame


def prediction_columns(protocol: FinalEvaluationProtocol, allowlist: Sequence[str]) -> list[str]:
    """Explicit stage-1 projection: keys + allowlist + registered hybrid inputs only."""
    cols = list(
        dict.fromkeys([*PREDICTION_KEYS, *allowlist, *protocol.stages.prediction_columns_extra])
    )
    forbidden = set(protocol.stages.forbidden_in_prediction) | set(TARGET_COLUMNS)
    bad = [
        c
        for c in cols
        if c in forbidden or (c.startswith("target") and c != "target_hour_utc") or "eligible" in c
    ]
    if bad:
        raise StageOrderError(f"outcome columns requested in the prediction stage: {bad}")
    return cols


def check_rows(frame: pd.DataFrame, window: EvaluationWindowSpec, what: str) -> None:
    """Exactly the expected origins x horizons, each once."""
    keys = frame[KEYS].assign(horizon=frame["horizon"].astype("int64"))
    dup = int(keys.duplicated().sum())
    if dup:
        raise RowContractError(f"{what}: {dup} duplicate (origin, horizon) rows")
    expected = pd.MultiIndex.from_product(
        [
            pd.DatetimeIndex([market_midnight_utc(d) for d in window_origins(window)]),
            range(1, window.horizons_per_origin + 1),
        ],
        names=KEYS,
    )
    got = pd.MultiIndex.from_frame(keys)
    missing, extra = expected.difference(got), got.difference(expected)
    if len(missing) or len(extra):
        raise RowContractError(f"{what}: {len(missing)} missing and {len(extra)} extra rows")
    if len(frame) != window.expected_rows or keys["origin_utc"].nunique() != (
        window.expected_origins
    ):
        raise RowContractError(f"{what}: {len(frame)} rows, expected {window.expected_rows}")


# --- artefact binding -----------------------------------------------------------------


def _same_records(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Exact equality after aligning columns; object (date) columns compared as text."""
    a, b = a.reset_index(drop=True), b[list(a.columns)].reset_index(drop=True)
    if len(a) != len(b):
        return False
    for col in a.columns:
        x, y = a[col], b[col]
        if x.dtype == object or y.dtype == object:
            x, y = x.astype(str), y.astype(str)
        if not np.array_equal(x.to_numpy(), y.to_numpy()):
            return False
    return True


def verify_artefact(
    artefact_dir: Path,
    protocol: FinalEvaluationProtocol,
    allowlist: Sequence[str],
    repo_root: Path,
    git: GitFn,
) -> dict[str, Any]:
    """Every identity pinned by the protocol, checked before any prediction."""
    spec = protocol.artefact
    manifest_path = artefact_dir / "pre_evaluation_manifest.json"
    if not manifest_path.is_file():
        raise EvaluationRefusedError(
            f"artefact manifest missing: {manifest_path}; nothing was read"
        )
    checks: dict[str, bool] = {}
    manifest_sha = sha256_file(manifest_path)
    checks["pre_evaluation_manifest_sha256"] = manifest_sha == spec.pre_evaluation_manifest_sha256
    m = read_json(manifest_path)

    checks["selected_candidate"] = m.get("candidate") == protocol.selected_candidate
    checks["fallback_identity"] = m.get("fallback") == protocol.benchmark
    record = repo_root / spec.candidate_record.path
    checks["candidate_record"] = (
        record.is_file()
        and sha256_file(record) == spec.candidate_record.sha256
        and m.get("benchmark_record_hashes", {}).get(spec.candidate_record.path)
        == spec.candidate_record.sha256
    )
    code = m.get("code", {})
    checks["model_producing_commit"] = (
        code.get("commit") == spec.model_producing_commit and code.get("dirty") is False
    )

    models: dict[str, dict[str, Any]] = {}
    for name, ms in spec.models.items():
        info = m.get("models", {}).get(name, {})
        path = artefact_dir / ms.file
        on_disk = sha256_file(path) if path.is_file() else None
        checks[f"model_{name}"] = (
            on_disk == ms.sha256 == info.get("sha256")
            and info.get("file") == ms.file
            and info.get("iterations") == ms.iterations
            and info.get("alpha") == ms.alpha
        )
        models[name] = {"file": ms.file, "sha256": on_disk, "iterations": ms.iterations}

    off_path = artefact_dir / spec.trailing_offsets.file
    off_sha = sha256_file(off_path) if off_path.is_file() else None
    ok = off_sha == spec.trailing_offsets.sha256
    if ok:
        table = pd.read_parquet(off_path).sort_values(["horizon", "quantile"], kind="mergesort")
        inline = pd.DataFrame(m.get("trailing_offsets", [])).sort_values(
            ["horizon", "quantile"], kind="mergesort"
        )
        ok = (
            len(table) == spec.trailing_offsets.rows
            and bool(table["calibrated"].all())
            and set(table.columns) == set(inline.columns)
            and _same_records(table, inline)
        )
    checks["trailing_offsets"] = ok

    hyb_path = artefact_dir / spec.hybrid_fallback.file
    hyb_sha = sha256_file(hyb_path) if hyb_path.is_file() else None
    ok = hyb_sha == spec.hybrid_fallback.sha256 == m.get("hybrid_fallback", {}).get("sha256")
    if ok:
        hyb = read_json(hyb_path)
        offsets = pd.DataFrame(hyb["offsets"])
        ok = (
            hyb["weight"] == spec.hybrid_fallback.weight == m["hybrid_fallback"]["weight"]
            and sorted(offsets["horizon"]) == list(range(1, 25))
            and bool(offsets["calibrated"].all())
        )
    checks["hybrid_fallback"] = ok

    checks["feature_allowlist_order"] = m.get("feature_allowlist") == list(allowlist)
    checks["feature_allowlist_sha256"] = (
        allowlist_sha256(allowlist) == spec.feature_allowlist_sha256
    )
    installed = importlib.metadata.version("lightgbm")
    checks["lightgbm_version"] = (
        installed == spec.lightgbm_version == m.get("environment", {}).get("lightgbm")
    )

    pinned = {t: s.model_dump() for t, s in spec.contract_tags.items()}
    checks["contract_tags_in_manifest"] = m.get("tags") == pinned
    checks["contract_tags_in_repository"] = all(
        git("rev-parse", t) == s["tag_object"]
        and git("rev-parse", f"{t}^{{commit}}") == s["commit"]
        for t, s in pinned.items()
    )

    hold = m.get("holdout", {})
    first = protocol.holdout.first_origin.isoformat()
    checks["holdout_boundary"] = (
        hold.get("first_origin") == first
        and hold.get("holdout_rows_loaded") == 0
        and hold.get("holdout_evaluated") is False
        and str(hold.get("max_origin_loaded", "9999")) < first
        and str(m.get("date_ranges", {}).get("final_training_origins", ["", "9999"])[1]) < first
    )
    checks["configuration_unchanged"] = bool(m.get("config_hashes")) and all(
        (repo_root / f).is_file() and sha256_file(repo_root / f) == h
        for f, h in m["config_hashes"].items()
    )
    inputs = m.get("inputs", {})
    checks["feature_run_identity"] = (
        inputs.get("feature_run_id") == protocol.inputs.feature_run_id
        and inputs.get("feature_run_manifest_sha256") == protocol.inputs.feature_run_manifest_sha256
        and inputs.get("feature_outputs_verified")
        == [{"path": f.path, "sha256": f.sha256} for f in protocol.inputs.files]
    )
    failed = sorted(k for k, v in checks.items() if not v)
    if failed:
        raise EvaluationRefusedError(f"artefact verification failed: {failed}; nothing was read")
    return {
        "artefact_run_id": spec.run_id,
        "pre_evaluation_manifest_sha256": manifest_sha,
        "model_producing_commit": spec.model_producing_commit,
        "candidate_record": spec.candidate_record.model_dump(),
        "models": models,
        "trailing_offsets_sha256": off_sha,
        "hybrid_fallback_sha256": hyb_sha,
        "hybrid_weight": spec.hybrid_fallback.weight,
        "feature_allowlist": list(allowlist),
        "feature_allowlist_sha256": allowlist_sha256(allowlist),
        "lightgbm_version": installed,
        "contract_tags": pinned,
        "config_hashes": m["config_hashes"],
        "checks": {k: checks[k] for k in sorted(checks)},
    }


# --- stage 1: prediction --------------------------------------------------------------


def predict_rows(
    art: FinalArtefact, inputs: pd.DataFrame, protocol: FinalEvaluationProtocol
) -> pd.DataFrame:
    """Candidate (raw + trailing offsets) and benchmark (hybrid + offsets); no sorting."""
    rows = inputs.sort_values(KEYS, kind="mergesort").reset_index(drop=True)
    base = rows[["origin_utc", "origin_market", "horizon", "target_hour_utc"]].copy()
    cand = art.predict(rows)
    c = base.assign(model=protocol.selected_candidate, fallback_used=False)
    for n in QNAMES:
        c[f"raw_{n}"] = cand[f"raw_{n}"].to_numpy()
        c[n] = cand[n].to_numpy()
    hybrid = DayWeekHybrid((art.hybrid_weight,))
    hybrid.weight = art.hybrid_weight
    raw = hybrid.predict(select_inputs(rows, hybrid)).reset_index(drop=True)
    fb = art.predict_fallback(rows)
    b = base.assign(model=protocol.benchmark, fallback_used=fb["fallback_used"].to_numpy())
    for n in QNAMES:
        b[f"raw_{n}"] = raw["point_raw"].to_numpy()
        b[n] = fb[n].to_numpy()
    preds = pd.concat([c, b], ignore_index=True)
    finite = np.isfinite(preds[list(QNAMES)].to_numpy(dtype=float)).all(axis=1)
    preds["available"] = finite
    cols = ["model", "origin_utc", "origin_market", "horizon", "target_hour_utc"]
    return preds[[*cols, *(f"raw_{n}" for n in QNAMES), *QNAMES, "available", "fallback_used"]]


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"{path} exists; outputs are never overwritten")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(tmp, index=False)
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    tmp.replace(path)
    path.chmod(0o444)


def _atomic_text(text: str, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"{path} exists; outputs are never overwritten")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


@dataclass
class StageLog:
    """Stage flags and timestamps; outcome access requires a valid seal."""

    output_dir: Path
    predictions_generated: bool = False
    predictions_sealed: bool = False
    targets_opened: bool = False
    metrics_calculated: bool = False
    seal: dict[str, Any] | None = None
    targets_first_opened_utc: str | None = None

    def require_sealed(self) -> dict[str, Any]:
        if not self.predictions_sealed or self.seal is None:
            raise StageOrderError("targets cannot be opened before predictions are sealed")
        path = self.output_dir / "predictions.parquet"
        if not path.is_file() or sha256_file(path) != self.seal["prediction_file_sha256"]:
            raise StageOrderError("sealed prediction file is missing or changed")
        return self.seal

    def flags(self) -> dict[str, Any]:
        return {
            "predictions_generated": self.predictions_generated,
            "predictions_sealed": self.predictions_sealed,
            "targets_opened": self.targets_opened,
            "metrics_calculated": self.metrics_calculated,
        }


def seal_predictions(preds: pd.DataFrame, log: StageLog) -> dict[str, Any]:
    """Write predictions atomically (read-only), verify exact reload, hash and seal."""
    path = log.output_dir / "predictions.parquet"
    _atomic_parquet(preds, path)
    reloaded = pd.read_parquet(path)
    pd.testing.assert_frame_equal(reloaded, preds.reset_index(drop=True), check_exact=True)
    origin_dates = preds["origin_market"].dt.date
    seal = {
        "prediction_file": "predictions.parquet",
        "prediction_file_sha256": sha256_file(path),
        "prediction_rows_generated": len(preds),
        "prediction_rows_by_model": {
            str(k): int(v) for k, v in preds["model"].value_counts().sort_index().items()
        },
        "max_origin_predicted": str(max(origin_dates)),
        "min_origin_predicted": str(min(origin_dates)),
        "exact_reload": True,
        "predictions_sealed_utc": _now(),
    }
    write_json_atomic(log.output_dir / "prediction_seal.json", seal)
    (log.output_dir / "prediction_seal.json").chmod(0o444)
    log.seal, log.predictions_sealed = seal, True
    return seal


# --- stage 2: evaluation --------------------------------------------------------------


def open_targets(
    log: StageLog,
    source: FeatureSource,
    window: EvaluationWindowSpec,
    on_first_open: Callable[[str], None],
) -> pd.DataFrame:
    """The only reader of target columns for the evaluation window (after the seal)."""
    log.require_sealed()
    opened = _now()
    on_first_open(opened)  # ledger records target access before the read
    log.targets_opened, log.targets_first_opened_utc = True, opened
    source.verify()
    cols = list(dict.fromkeys([*KEYS, "origin_market", *TARGET_COLUMNS, *SLICE_COLUMNS]))
    return source.read(cols, window.first_origin, window.last_origin)


def high_demand_threshold(
    log: StageLog,
    source: FeatureSource,
    training_last: dt.date,
    window_first: dt.date,
    quantile: float,
    frozen_mw: float | None,
    frozen_rows: int | None,
) -> dict[str, Any]:
    """Training-defined threshold: q of available targets with origin <= training_last."""
    log.require_sealed()
    if training_last >= window_first:
        raise HoldoutAccessError("high-demand training rows must precede the evaluation window")
    rows = source.read(["origin_utc", *TARGET_COLUMNS], None, training_last)
    y = rows[TARGET].where(rows["target_available"]).dropna()
    value = float(np.quantile(y, quantile))
    if frozen_mw is not None and (
        abs(value - frozen_mw) > THRESHOLD_TOLERANCE_MW or len(y) != frozen_rows
    ):
        raise ValueError(f"high-demand threshold {value} ({len(y)} rows) != frozen {frozen_mw}")
    return {
        "threshold_mw": value,
        "training_rows": len(y),
        "training_last_origin": training_last.isoformat(),
        "quantile": quantile,
        "frozen_threshold_mw": frozen_mw,
    }


def summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Frozen metric set (``metrics.summarise``) plus lower/upper tail-miss rates."""
    out = summarise(frame)
    rows = frame[frame["available"] & frame["actual"].notna()]
    if not rows.empty:
        y = rows["actual"].to_numpy(dtype=float)
        out["lower_tail_miss_rate"] = float(np.mean(y < rows["p10"].to_numpy(dtype=float)))
        out["upper_tail_miss_rate"] = float(np.mean(y > rows["p90"].to_numpy(dtype=float)))
    return out


def _table(frame: pd.DataFrame, dim: str) -> list[dict[str, Any]]:
    return [
        {"model": str(m), dim: _py(k), **summary(g)}
        for (m, k), g in frame.groupby(["model", dim], sort=True)
    ]


def _crossing(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    rows = frame[frame["available"]]
    p10, p50, p90 = (rows[f"{prefix}{n}"].to_numpy(dtype=float) for n in QNAMES)
    crossed = (p10 > p50) | (p50 > p90)
    return {
        "rows": len(rows),
        "rows_crossed": int(crossed.sum()),
        "rate": crossing_rate(p10, p50, p90),
        "mean_magnitude_mw": crossing_magnitude(p10, p50, p90),
        "p10_above_p50": int((p10 > p50).sum()),
        "p50_above_p90": int((p50 > p90).sum()),
    }


def _row_pinball(g: pd.DataFrame) -> pd.Series:
    y = g["actual"].to_numpy(dtype=float)
    losses = [
        np.maximum(q * (y - g[n].to_numpy(dtype=float)), (q - 1) * (y - g[n].to_numpy(dtype=float)))
        for n, q in QUANTILE_OF.items()
    ]
    return pd.Series(np.mean(losses, axis=0), index=g.index)


def evaluate(
    preds: pd.DataFrame,
    targets: pd.DataFrame,
    threshold_mw: float,
    bcfg: BacktestConfig,
    protocol: FinalEvaluationProtocol,
    window: EvaluationWindowSpec,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    check_rows(targets, window, "targets")
    cand, bench = protocol.selected_candidate, protocol.benchmark
    for model in (cand, bench):
        check_rows(preds[preds["model"] == model], window, f"predictions[{model}]")
    t = targets.assign(horizon=targets["horizon"].astype(preds["horizon"].dtype))
    ev = attach_evaluation(preds.assign(fold_id=0), t, {0: threshold_mw}, bcfg)
    ev["origin_date"] = ev["origin_market"].dt.date
    ev["origin_month"] = ev["origin_market"].dt.month.astype("int64")
    ok = ev.assign(ok=ev["available"] & ev["actual"].notna()).pivot_table(
        index=KEYS, columns="model", values="ok", aggfunc="first"
    )
    ident_keys = ok[ok[[cand, bench]].all(axis=1)].index
    keyed = ev.set_index(KEYS)
    ident = keyed[keyed.index.isin(ident_keys)].reset_index()
    a = ident[ident["model"] == cand].set_index(KEYS).loc[ident_keys]
    b = ident[ident["model"] == bench].set_index(KEYS).loc[ident_keys]
    boot = protocol.bootstrap
    paired = [
        {
            "metric": "mae_mw",
            "model_a": cand,
            "model_b": bench,
            **paired_block_bootstrap(
                a["p50"] - a["actual"],
                b["p50"] - b["actual"],
                a["origin_date"],
                boot.replicates,
                boot.seed,
                boot.confidence,
            ),
        },
        {
            "metric": "mean_pinball",
            "model_a": cand,
            "model_b": bench,
            **paired_block_bootstrap(
                _row_pinball(a),
                _row_pinball(b),
                a["origin_date"],
                boot.replicates,
                boot.seed,
                boot.confidence,
            ),
        },
    ]
    paired[1]["mean_pinball_difference_mw"] = paired[1].pop("mae_difference_mw")
    target_available = int(t["target_available"].sum())
    seasons = sorted({str(s) for s in ev["season"].dropna().unique()})
    results: dict[str, Any] = {
        "protocol_id": protocol.protocol_id,
        "decision_rule": protocol.decision_rule,
        "headline_models": [cand, bench],
        "quantile_order": protocol.quantile_order,
        "row_reconciliation": {
            "expected_rows_per_model": window.expected_rows,
            "expected_origins": window.expected_origins,
            "prediction_rows_available": {
                str(m): int(g["available"].sum()) for m, g in ev.groupby("model")
            },
            "target_rows_loaded": len(t),
            "target_rows_available": target_available,
            "target_rows_unavailable": len(t) - target_available,
            "identical_rows": len(ident_keys),
            "identical_origins": int(pd.Index(ident_keys.get_level_values(0)).nunique()),
            "identical_row_horizons": sorted({int(h) for h in ident_keys.get_level_values(1)}),
        },
        "headline_identical_rows": {str(m): summary(g) for m, g in ident.groupby("model")},
        "own_rows": {str(m): summary(g) for m, g in ev.groupby("model")},
        "slices_identical_rows": {dim: _table(ident, dim) for dim in protocol.slices.dimensions},
        "quantile_crossing_frozen_order": {
            str(m): {"calibrated": _crossing(g, ""), "raw": _crossing(g, "raw_")}
            for m, g in ev.groupby("model")
        },
        "paired_bootstrap_identical_rows": paired,
        "high_demand_threshold_mw": threshold_mw,
        "seasons_observed": seasons,
        "seasons_match_protocol": seasons == sorted(protocol.slices.expected_seasons),
    }
    return ev, results


def metrics_markdown(results: dict[str, Any], mode: str, run_id: str) -> str:
    label = (
        "REHEARSAL ONLY - development origins with in-sample models; not performance evidence; "
        "no benchmark or selection is updated."
        if mode == "rehearsal"
        else "Final one-time holdout evaluation (descriptive and final)."
    )
    rec = results["row_reconciliation"]
    lines = [
        f"# Evaluation `{run_id}`",
        "",
        f"**{label}**",
        "",
        f"Identical rows: {rec['identical_rows']} of {rec['expected_rows_per_model']} "
        f"({rec['identical_origins']} origins); target rows available "
        f"{rec['target_rows_available']} of {rec['target_rows_loaded']}.",
        "",
        "| Model | MAE (MW) | RMSE (MW) | WAPE | Bias (MW) | Mean pinball (MW) | p10 / p50 / p90 "
        "pinball | Coverage | Width (MW) | Below p10 | Above p90 | Crossing rate |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m, s in results["headline_identical_rows"].items():
        lines.append(
            f"| {m} | {s['mae_mw']:.1f} | {s['rmse_mw']:.1f} | {s['wape']:.2%} | "
            f"{s['mean_signed_error_mw']:.1f} | {s['mean_pinball']:.1f} | "
            f"{s['pinball_p10']:.1f} / {s['pinball_p50']:.1f} / {s['pinball_p90']:.1f} | "
            f"{s['coverage_p10_p90']:.1%} | {s['mean_interval_width_mw']:.1f} | "
            f"{s['lower_tail_miss_rate']:.1%} | {s['upper_tail_miss_rate']:.1%} | "
            f"{s['crossing_rate']:.2%} |"
        )
    lines += ["", "Paired market-date block bootstrap (candidate minus benchmark):", ""]
    for p in results["paired_bootstrap_identical_rows"]:
        diff = p.get("mae_difference_mw", p.get("mean_pinball_difference_mw"))
        lines.append(
            f"- {p['metric']}: {diff:.1f} MW, 95% CI [{p['ci_low']:.1f}, {p['ci_high']:.1f}] "
            f"({p['dates']} dates, {p['replicates']} replicates, seed {p['seed']})"
        )
    lines += ["", "Quantile crossing in frozen order (never sorted):", ""]
    for m, c in results["quantile_crossing_frozen_order"].items():
        lines.append(
            f"- {m}: calibrated {c['calibrated']['rows_crossed']} rows "
            f"({c['calibrated']['rate']:.2%}), raw {c['raw']['rows_crossed']} rows"
        )
    lines += ["", "Slice tables (identical rows) are in `metrics.json`.", ""]
    return "\n".join(lines)


# --- ledger and orchestration ---------------------------------------------------------


class Ledger:
    """One file per (protocol, artefact, mode); refuses repeats after success."""

    def __init__(self, path: Path, protocol_id: str, artefact_run_id: str, mode: str) -> None:
        self.path, self.mode = path, mode
        self.data: dict[str, Any] = (
            read_json(path)
            if path.is_file()
            else {
                "protocol_id": protocol_id,
                "artefact_run_id": artefact_run_id,
                "mode": mode,
                "status": "open",
                "completed_run_id": None,
                "attempts": [],
            }
        )
        if (self.data["protocol_id"], self.data["artefact_run_id"], self.data["mode"]) != (
            protocol_id,
            artefact_run_id,
            mode,
        ):
            raise EvaluationRefusedError(f"ledger {path} belongs to another protocol/artefact/mode")

    def check_start(self, run_id: str, acknowledge_prior_target_access: bool) -> bool:
        """Return whether this attempt can still be a pristine first evaluation."""
        if self.data["status"] == "complete":
            raise EvaluationRefusedError(
                f"a successful evaluation ({self.data['completed_run_id']}) already exists for "
                "this artefact and protocol; repeats are refused; nothing was read"
            )
        if any(a["run_id"] == run_id for a in self.data["attempts"]):
            raise EvaluationRefusedError(f"run ID {run_id} was already attempted; nothing was read")
        opened = [a["run_id"] for a in self.data["attempts"] if a.get("targets_opened")]
        if opened and not acknowledge_prior_target_access:
            raise EvaluationRefusedError(
                f"earlier attempt(s) {opened} opened targets; a rerun must be disclosed with "
                "--acknowledge-prior-target-access and is not a pristine first evaluation; "
                "nothing was read"
            )
        return not opened

    def update(self, run_id: str, **fields: Any) -> None:
        attempts = self.data["attempts"]
        current = next((a for a in attempts if a["run_id"] == run_id), None)
        if current is None:
            current = {"run_id": run_id}
            attempts.append(current)
        current.update(fields)
        write_json_atomic(self.path, self.data, exclusive=False)

    def complete(self, run_id: str) -> None:
        self.data["status"], self.data["completed_run_id"] = "complete", run_id
        self.update(run_id, status="complete")


@dataclass(frozen=True)
class EvaluationSpec:
    mode: str
    run_id: str
    artefact_dir: Path
    output_dir: Path
    ledger_path: Path
    window: EvaluationWindowSpec
    features: FeatureSource
    threshold_training_last: dt.date
    frozen_threshold_mw: float | None
    frozen_threshold_rows: int | None
    acknowledge_prior_target_access: bool = False


def pre_execution_record(
    spec: EvaluationSpec,
    protocol: FinalEvaluationProtocol,
    identities: dict[str, Any],
    inputs: dict[str, Any],
    columns: list[str],
    code: dict[str, Any],
    repo_root: Path,
    ledger_attempts: int,
) -> dict[str, Any]:
    """Deterministic (timestamp-free) record of everything verified before prediction."""
    return {
        "record": "pre-execution manifest",
        "mode": spec.mode,
        "run_id": spec.run_id,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": sha256_file(repo_root / PROTOCOL_FILE),
        "evaluation_code_commit": code.get("commit"),
        "evaluation_code_dirty": code.get("dirty"),
        "model_producing_commit": identities["model_producing_commit"],
        "artefact": identities,
        "inputs": inputs,
        "window": {
            "first_origin": spec.window.first_origin.isoformat(),
            "last_origin": spec.window.last_origin.isoformat(),
            "expected_origins": spec.window.expected_origins,
            "expected_rows": spec.window.expected_rows,
            "horizons_per_origin": spec.window.horizons_per_origin,
        },
        "prediction_stage_columns": columns,
        "forbidden_in_prediction": list(protocol.stages.forbidden_in_prediction),
        "high_demand": {
            "training_last_origin": spec.threshold_training_last.isoformat(),
            "frozen_threshold_mw": spec.frozen_threshold_mw,
            "frozen_training_rows": spec.frozen_threshold_rows,
        },
        "bootstrap": protocol.bootstrap.model_dump(),
        "ledger_prior_attempts": ledger_attempts,
        "evaluation_feature_rows_loaded": 0,
        "evaluation_target_rows_loaded": 0,
        "command": FINAL_COMMAND.format(artefact=protocol.artefact.run_id, run_id=spec.run_id)
        if spec.mode == "final_holdout"
        else None,
    }


def _check_spec(
    spec: EvaluationSpec, protocol: FinalEvaluationProtocol, code: dict[str, Any], clean: bool
) -> None:
    if spec.mode not in MODES:
        raise EvaluationRefusedError(f"unknown mode {spec.mode}")
    if spec.mode == "final_holdout" and spec.window != protocol.holdout:
        raise EvaluationRefusedError("final evaluation must use the frozen holdout window")
    if spec.mode == "rehearsal" and spec.window.last_origin >= protocol.holdout.first_origin:
        raise HoldoutAccessError("rehearsal origins must precede the holdout; nothing was read")
    if spec.threshold_training_last != spec.window.first_origin - dt.timedelta(days=2):
        raise EvaluationRefusedError(
            "high-demand training rows must end at the last matured origin"
        )
    if clean and code.get("dirty") is not False:
        raise EvaluationRefusedError(
            f"a clean, committed git tree is required: {code}; nothing was read"
        )


def preflight(
    spec: EvaluationSpec,
    protocol: FinalEvaluationProtocol,
    allowlist: Sequence[str],
    repo_root: Path,
    code: dict[str, Any],
    git: GitFn,
    require_clean: bool = True,
) -> dict[str, Any]:
    """All binding checks with no feature or target row decoded."""
    _check_spec(spec, protocol, code, require_clean)
    if spec.output_dir.exists():
        raise FileExistsError(f"{spec.output_dir} exists; run IDs are never reused")
    ledger = Ledger(spec.ledger_path, protocol.protocol_id, protocol.artefact.run_id, spec.mode)
    ledger.check_start(spec.run_id, spec.acknowledge_prior_target_access)
    identities = verify_artefact(spec.artefact_dir, protocol, allowlist, repo_root, git)
    inputs = spec.features.verify()
    columns = prediction_columns(protocol, allowlist)
    return pre_execution_record(
        spec, protocol, identities, inputs, columns, code, repo_root, len(ledger.data["attempts"])
    )


def run_evaluation(
    spec: EvaluationSpec,
    protocol: FinalEvaluationProtocol,
    bcfg: BacktestConfig,
    allowlist: Sequence[str],
    repo_root: Path,
    code: dict[str, Any],
    git: GitFn,
    require_clean: bool = True,
) -> dict[str, Any]:
    """Verify -> predict -> seal -> open targets -> metrics. Failure manifests record stages."""
    _check_spec(spec, protocol, code, require_clean)
    if spec.output_dir.exists():
        raise FileExistsError(f"{spec.output_dir} exists; run IDs are never reused")
    ledger = Ledger(spec.ledger_path, protocol.protocol_id, protocol.artefact.run_id, spec.mode)
    pristine = ledger.check_start(spec.run_id, spec.acknowledge_prior_target_access)
    spec.output_dir.mkdir(parents=True, exist_ok=False)
    started = _now()
    log = StageLog(spec.output_dir)
    ledger.update(spec.run_id, started_utc=started, status="started", **log.flags())
    try:
        identities = verify_artefact(spec.artefact_dir, protocol, allowlist, repo_root, git)
        inputs_verified = spec.features.verify()
        columns = prediction_columns(protocol, allowlist)
        pre = pre_execution_record(
            spec,
            protocol,
            identities,
            inputs_verified,
            columns,
            code,
            repo_root,
            len(ledger.data["attempts"]) - 1,
        )
        write_json_atomic(spec.output_dir / "pre_execution_manifest.json", pre)
        pre_sha = sha256_file(spec.output_dir / "pre_execution_manifest.json")

        # stage 1: prediction (keys + approved features only)
        art = load_final_artefact(spec.artefact_dir)
        inputs = spec.features.read(columns, spec.window.first_origin, spec.window.last_origin)
        leaked = set(inputs.columns) & {*TARGET_COLUMNS, *protocol.stages.forbidden_in_prediction}
        if leaked:
            raise StageOrderError(f"outcome columns present in the prediction stage: {leaked}")
        if set(inputs["availability_profile"]) != {protocol.inputs.profile}:
            raise RowContractError("prediction inputs are not all from the primary profile")
        check_rows(inputs, spec.window, "prediction inputs")
        preds = predict_rows(art, inputs, protocol)
        log.predictions_generated = True
        seal = seal_predictions(preds, log)
        ledger.update(
            spec.run_id, **log.flags(), prediction_file_sha256=seal["prediction_file_sha256"]
        )

        # stage 2: evaluation (only after the seal)
        def record_open(opened: str) -> None:
            ledger.update(spec.run_id, targets_opened=True, targets_first_opened_utc=opened)

        targets = open_targets(log, spec.features, spec.window, record_open)
        threshold = high_demand_threshold(
            log,
            spec.features,
            spec.threshold_training_last,
            spec.window.first_origin,
            bcfg.slices.high_demand_training_quantile,
            spec.frozen_threshold_mw,
            spec.frozen_threshold_rows,
        )
        ev, results = evaluate(
            preds, targets, threshold["threshold_mw"], bcfg, protocol, spec.window
        )
        log.metrics_calculated = True
        log.require_sealed()  # predictions unchanged after outcome access
        results["high_demand"] = threshold
        results["mode"] = spec.mode
        if spec.mode == "rehearsal":
            results["rehearsal_only"] = True
        _atomic_parquet(
            ev.drop(columns=["civil_date", "origin_date"]),
            spec.output_dir / "evaluation_rows.parquet",
        )
        _atomic_text(
            json.dumps(results, indent=2, sort_keys=True, default=str) + "\n",
            spec.output_dir / "metrics.json",
        )
        _atomic_text(
            metrics_markdown(results, spec.mode, spec.run_id), spec.output_dir / "metrics.md"
        )
        rec = results["row_reconciliation"]
        manifest = {
            "run_id": spec.run_id,
            "mode": spec.mode,
            "status": "complete",
            "rehearsal_only": spec.mode == "rehearsal",
            "updates_benchmark_or_selection": False,
            "decision_rule": protocol.decision_rule,
            "pristine_first_evaluation": pristine,
            "created_utc": started,
            "completed_utc": _now(),
            "protocol_id": protocol.protocol_id,
            "protocol_sha256": pre["protocol_sha256"],
            "pre_execution_manifest_sha256": pre_sha,
            "model_producing_commit": identities["model_producing_commit"],
            "evaluation_code": code,
            "artefact_run_id": protocol.artefact.run_id,
            "instrumentation": {
                "prediction_rows_generated": seal["prediction_rows_generated"],
                "prediction_rows_by_model": seal["prediction_rows_by_model"],
                "max_origin_predicted": seal["max_origin_predicted"],
                "prediction_file_sha256": seal["prediction_file_sha256"],
                "predictions_sealed_utc": seal["predictions_sealed_utc"],
                "targets_first_opened_utc": log.targets_first_opened_utc,
                "target_rows_loaded": rec["target_rows_loaded"],
                "pre_window_training_target_rows_loaded": threshold["training_rows"],
                "identical_row_reconciliation": rec,
                "holdout_evaluation_executed": spec.mode == "final_holdout",
                "holdout_target_rows_loaded": (
                    rec["target_rows_loaded"] if spec.mode == "final_holdout" else 0
                ),
            },
            "stages": log.flags(),
            "outputs": {
                p.name: sha256_file(p) for p in sorted(spec.output_dir.iterdir()) if p.is_file()
            },
        }
        write_json_atomic(spec.output_dir / "run_manifest.json", manifest)
        ledger.update(spec.run_id, **log.flags(), completed_utc=manifest["completed_utc"])
        ledger.complete(spec.run_id)
        return manifest
    except Exception as exc:
        flags = log.flags()
        failure = {
            "run_id": spec.run_id,
            "mode": spec.mode,
            "status": "failed",
            "created_utc": started,
            "failed_utc": _now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            **flags,
            "targets_first_opened_utc": log.targets_first_opened_utc,
            "retry_allowed_after_review": not log.targets_opened,
            "pristine_first_evaluation_still_possible": pristine and not log.targets_opened,
            "disclosure": (
                "targets were opened before this failure; any later evaluation must disclose "
                "it and cannot be called a pristine first evaluation"
                if log.targets_opened
                else "no target column was opened; a retry with a new run ID is allowed "
                "after review"
            ),
            "evaluation_code": code,
        }
        write_json_atomic(spec.output_dir / "failure_manifest.json", failure)
        ledger.update(spec.run_id, status="failed", error_type=type(exc).__name__, **flags)
        raise


# --- spec builders ------------------------------------------------------------------


def final_spec(
    root: Path, protocol: FinalEvaluationProtocol, run_id: str, acknowledge: bool = False
) -> EvaluationSpec:
    inp = protocol.inputs
    return EvaluationSpec(
        mode="final_holdout",
        run_id=run_id,
        artefact_dir=root / "results" / "runs" / protocol.artefact.run_id,
        output_dir=root / "results" / "runs" / run_id,
        ledger_path=root
        / "results"
        / "holdout_ledger"
        / f"{protocol.protocol_id}__{protocol.artefact.run_id}.json",
        window=protocol.holdout,
        features=FeatureSource(
            root=root,
            files=tuple((f.path, f.sha256) for f in inp.files),
            label=f"{inp.feature_run_id}/{inp.profile}",
            manifest=(
                f"results/runs/{inp.feature_run_id}/run_manifest.json",
                inp.feature_run_manifest_sha256,
            ),
        ),
        threshold_training_last=protocol.slices.high_demand.training_last_origin,
        frozen_threshold_mw=protocol.slices.high_demand.threshold_mw,
        frozen_threshold_rows=protocol.slices.high_demand.training_rows,
        acknowledge_prior_target_access=acknowledge,
    )


def run_rehearsal(
    root: Path,
    protocol: FinalEvaluationProtocol,
    bcfg: BacktestConfig,
    allowlist: Sequence[str],
    rehearsal_run_id: str,
    code: dict[str, Any],
    git: GitFn,
    require_clean: bool = True,
) -> dict[str, Any]:
    """End-to-end rehearsal on development origins with copied artefact and inputs.

    Only pre-holdout rows are copied (filtered at read); the copy is the only input the
    rehearsal evaluator sees. Labelled rehearsal-only; nothing is selected or updated.
    """
    if require_clean and code.get("dirty") is not False:
        raise EvaluationRefusedError(f"a clean, committed git tree is required: {code}")
    holdout_first = protocol.holdout.first_origin
    sandbox = root / "results" / "runs" / rehearsal_run_id
    sandbox.mkdir(parents=True, exist_ok=False)
    source_art = root / "results" / "runs" / protocol.artefact.run_id
    art_copy = sandbox / "artefact_copy" / protocol.artefact.run_id
    shutil.copytree(source_art, art_copy)
    copied = {
        str(p.relative_to(source_art)): sha256_file(p)
        for p in sorted(source_art.rglob("*"))
        if p.is_file()
    }
    if copied != {
        str(p.relative_to(art_copy)): sha256_file(p)
        for p in sorted(art_copy.rglob("*"))
        if p.is_file()
    }:
        raise EvaluationRefusedError("artefact copy differs from the source artefact")

    real = final_spec(root, protocol, "unused").features
    real.verify()
    names = pq.read_schema(real.root / real.files[0][0]).names
    rows = real.read(names, None, holdout_first - dt.timedelta(days=1))
    max_origin = max(rows["origin_market"].dt.date)
    if max_origin >= holdout_first:
        raise HoldoutAccessError("holdout rows reached the rehearsal copy")
    (sandbox / "inputs").mkdir()
    copy_path = sandbox / "inputs" / "pre_holdout_rows.parquet"
    rows.to_parquet(copy_path, index=False)
    copy_path.chmod(0o444)
    source = FeatureSource(
        root=sandbox,
        files=(("inputs/pre_holdout_rows.parquet", sha256_file(copy_path)),),
        label="rehearsal copy of pre-holdout rows",
    )
    window = protocol.rehearsal
    spec = EvaluationSpec(
        mode="rehearsal",
        run_id=f"{rehearsal_run_id}__evaluation",
        artefact_dir=art_copy,
        output_dir=sandbox / "evaluation",
        ledger_path=sandbox / "ledger.json",
        window=window,
        features=source,
        threshold_training_last=window.first_origin - dt.timedelta(days=2),
        frozen_threshold_mw=None,
        frozen_threshold_rows=None,
    )
    pre = preflight(spec, protocol, allowlist, root, code, git, require_clean)
    manifest = run_evaluation(spec, protocol, bcfg, allowlist, root, code, git, require_clean)
    refusals: dict[str, str] = {}
    for label, attempt in (
        (
            "repeat_after_success",
            replace(spec, run_id=f"{spec.run_id}_repeat", output_dir=sandbox / "evaluation_repeat"),
        ),
        ("overwrite_existing_output", spec),
    ):
        try:
            run_evaluation(attempt, protocol, bcfg, allowlist, root, code, git, require_clean)
        except (EvaluationRefusedError, FileExistsError) as exc:
            refusals[label] = f"{type(exc).__name__}: {exc}"
        else:  # pragma: no cover - a second success would be a defect
            raise EvaluationRefusedError(f"rehearsal {label} was not refused")
    record = {
        "rehearsal_run_id": rehearsal_run_id,
        "rehearsal_only": True,
        "updates_benchmark_or_selection": False,
        "note": (
            "The final models were trained on origins <= 2026-06-29 and calibrated on "
            "2026-01-01 .. 06-29, so rehearsal origins are in-sample: rehearsal metrics are "
            "pipeline checks, not performance evidence."
        ),
        "window": [window.first_origin.isoformat(), window.last_origin.isoformat()],
        "artefact_copy_files": copied,
        "input_copy": {
            "rows": len(rows),
            "max_origin": str(max_origin),
            "holdout_rows_copied": 0,
            "sha256": source.files[0][1],
        },
        "preflight_record_sha256": hashlib.sha256(
            json.dumps(pre, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "evaluation_manifest_sha256": sha256_file(spec.output_dir / "run_manifest.json"),
        "pre_execution_manifest_sha256": manifest["pre_execution_manifest_sha256"],
        "instrumentation": manifest["instrumentation"],
        "refusals": refusals,
        "evaluation_code": code,
    }
    write_json_atomic(sandbox / "rehearsal_manifest.json", record)
    return record
