"""Validated forecast-contract configuration.

Version-1 scope fields are typed as ``Literal`` so that a YAML edit alone
cannot silently change the region, target, unit, origin, horizon, or primary
feature set. Changing them is an owner decision (docs/decisions.md) plus a
code change.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nem_forecast.paths import ProjectPaths


class ConfigError(RuntimeError):
    """Raised when a config file is missing, unparsable, or violates the contract."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetConfig(_Frozen):
    region: Literal["VIC1"]
    source_field: Literal["TOTALDEMAND"]
    unit: Literal["MW"]
    native_interval_minutes: Literal[5]
    hourly_aggregation: Literal["time_weighted_mean_mw"]


class TimeConfig(_Frozen):
    market_utc_offset_hours: Literal[10]
    interval_label: Literal["start"]
    interval_closed: Literal["left"]

    @property
    def market_timezone(self) -> dt.timezone:
        """Fixed-offset NEM market timezone (no daylight saving)."""
        return dt.timezone(dt.timedelta(hours=self.market_utc_offset_hours), name="NEM")


class ForecastSettings(_Frozen):
    origin_market_time: Literal["00:00"]
    horizon_hours: Literal[24]
    quantiles: tuple[float, ...]
    primary_feature_set: Literal["demand_calendar"]
    weather_in_primary_model: Literal[False]

    @field_validator("quantiles")
    @classmethod
    def _check_quantiles(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if value != (0.1, 0.5, 0.9):
            raise ValueError("version-1 quantiles are fixed to [0.1, 0.5, 0.9]")
        return value

    @property
    def origin_time(self) -> dt.time:
        return dt.time.fromisoformat(self.origin_market_time)


class HistoryConfig(_Frozen):
    start_date: dt.date
    end_date: dt.date | None = Field(
        default=None,
        description="None = latest complete market date, frozen in the data snapshot manifest.",
    )

    @model_validator(mode="after")
    def _check_order(self) -> HistoryConfig:
        if self.start_date != dt.date(2022, 1, 1):
            raise ValueError("version-1 history start is fixed to 2022-01-01")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("history.end_date must not precede history.start_date")
        return self


class QualityConfig(_Frozen):
    min_hourly_interval_coverage: float = Field(gt=0.0, le=1.0)
    exclude_incomplete_hours_from_model_data: bool


class ForecastConfig(_Frozen):
    contract_version: Literal[1]
    target: TargetConfig
    time: TimeConfig
    forecast: ForecastSettings
    history: HistoryConfig
    quality: QualityConfig


def parse_forecast_config(data: Any) -> ForecastConfig:
    """Validate an already-parsed mapping, converting errors to :class:`ConfigError`."""
    if not isinstance(data, dict):
        raise ConfigError("forecast config must be a YAML mapping")
    try:
        return ForecastConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"forecast config violates the contract:\n{exc}") from exc


def load_forecast_config(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> ForecastConfig:
    """Load and validate the forecast config, resolving relative paths against the root."""
    paths = paths or ProjectPaths.from_root()
    config_path = paths.forecast_config_path(path)
    if not config_path.is_file():
        raise ConfigError(f"forecast config not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    return parse_forecast_config(data)


class ApprovedWindow(_Frozen):
    """Market-date window, end exclusive: intervals in [start 00:00, end 00:00) market time."""

    start_date: dt.date
    end_date: dt.date

    @model_validator(mode="after")
    def _check_order(self) -> ApprovedWindow:
        if self.end_date <= self.start_date:
            raise ValueError("approved_window.end_date must be after start_date")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days


class HttpConfig(_Frozen):
    timeout_seconds: float = Field(gt=0)
    max_attempts: int = Field(ge=1, le=10)
    backoff_seconds: float = Field(ge=0)
    user_agent: str = Field(min_length=1)


_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


class HistoricalSnapshotConfig(_Frozen):
    start_month: str = Field(pattern=_MONTH)
    end_month: str = Field(pattern=_MONTH)
    batch_size: int = Field(ge=1, le=60)
    pause_seconds_between_batches: float = Field(ge=0)
    snapshot_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    lock_manifest: str | None = Field(
        default=None, pattern=r"^configs/data_snapshots/[^/]+\.lock\.json$"
    )

    @model_validator(mode="after")
    def _check_order(self) -> HistoricalSnapshotConfig:
        if self.end_month < self.start_month:
            raise ValueError("end_month must not precede start_month")
        return self

    @property
    def months(self) -> list[tuple[int, int]]:
        year, month = (int(x) for x in self.start_month.split("-"))
        end = tuple(int(x) for x in self.end_month.split("-"))
        out = []
        while (year, month) <= end:
            out.append((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return out


class AemoSourceConfig(_Frozen):
    source_id: Literal["aemo_nemweb_mmsdm"]
    base_url: Literal["https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM"]
    table: Literal["DISPATCHREGIONSUM"]
    approved_window: ApprovedWindow
    max_window_days_without_owner_approval: int = Field(ge=1)
    http: HttpConfig
    historical_snapshot: HistoricalSnapshotConfig


def load_aemo_source_config(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> AemoSourceConfig:
    """Load and validate ``configs/aemo_source.yaml`` (or ``path``, resolved against the root)."""
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/aemo_source.yaml")
    if not config_path.is_file():
        raise ConfigError(f"AEMO source config not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return AemoSourceConfig.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"AEMO source config is invalid:\n{exc}") from exc


class PrimaryAvailability(_Frozen):
    availability_lag_hours: Literal[1]
    experiment_type: Literal["operational"]
    label: str = Field(min_length=1)


class SensitivityAvailability(_Frozen):
    availability_lag_hours: Literal[0]
    experiment_type: Literal["sensitivity"]
    label: str = Field(pattern=r"^SENSITIVITY ONLY")


class FeatureAvailabilityConfig(_Frozen):
    """Owner decision D-07: primary 1 h buffer; zero-lag kept only as labelled sensitivity."""

    primary_operational: PrimaryAvailability
    sensitivity_zero_lag: SensitivityAvailability


def load_feature_availability_config(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> FeatureAvailabilityConfig:
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/feature_availability.yaml")
    if not config_path.is_file():
        raise ConfigError(f"feature availability config not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return FeatureAvailabilityConfig.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"feature availability config is invalid:\n{exc}") from exc


class CivilCalendarConfig(_Frozen):
    timezone: Literal["Australia/Melbourne"]
    holidays_country: Literal["AU"]
    holidays_subdivision: Literal["VIC"]


class SameHourConfig(_Frozen):
    recent_available_days: Literal[7]
    same_weekday_weeks: Literal[4]


class HistoricalDemandConfig(_Frozen):
    target_relative_lags_hours: tuple[Literal[24], Literal[48], Literal[168]]
    recent_windows: dict[Literal["24", "168"], tuple[str, ...]]
    same_hour: SameHourConfig

    @field_validator("recent_windows")
    @classmethod
    def _check_windows(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        if value != {"24": ("mean", "std", "min", "max"), "168": ("mean", "std")}:
            raise ValueError("recent_windows must match the registered feature set v1")
        return value


class FeatureConfig(_Frozen):
    """Phase 3 feature set v1. Fields are fixed so unregistered features cannot be enabled."""

    feature_set_version: Literal[1]
    input_phase2_run_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    profiles: tuple[Literal["primary_operational"], Literal["sensitivity_zero_lag"]]
    civil_calendar: CivilCalendarConfig
    historical_demand: HistoricalDemandConfig


def load_feature_config(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> FeatureConfig:
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/features.yaml")
    if not config_path.is_file():
        raise ConfigError(f"feature config not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return FeatureConfig.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"feature config is invalid:\n{exc}") from exc


HOLDOUT_FIRST_ORIGIN = dt.date(2026, 7, 1)


class BacktestInputs(_Frozen):
    feature_contract_tag: Literal["feature-contract-v1"]
    feature_run_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    primary_profile: Literal["primary_operational"]
    target_column: Literal["target_dispatch_totaldemand_hourly_mean_mw"]


class BacktestMetrics(_Frozen):
    primary: Literal["mae_mw"]
    secondary: Literal["wape_aggregate"]


class DevelopmentWindow(_Frozen):
    first_origin: dt.date
    last_origin: dt.date
    fold_frequency: Literal["monthly"]

    @model_validator(mode="after")
    def _check(self) -> DevelopmentWindow:
        if self.last_origin < self.first_origin:
            raise ValueError("development last_origin precedes first_origin")
        if self.last_origin >= HOLDOUT_FIRST_ORIGIN:
            raise ValueError("development origins must end before the final holdout (2026-07-01)")
        if self.first_origin.day != 1:
            raise ValueError("monthly folds must start on the first of a month")
        return self


class HoldoutWindow(_Frozen):
    first_origin: Literal["2026-07-01"]
    last_origin: Literal["2026-09-30"]


class TrainingPolicy(_Frozen):
    policy: Literal["expanding"]
    maturity_lag_hours: Literal[1]


class CalibrationConfig(_Frozen):
    method: Literal["empirical_additive_residual"]
    window_days: Literal[180]
    min_samples_per_horizon: int = Field(ge=30, le=180)
    quantile_interpolation: Literal["linear"]
    quantiles: tuple[float, ...]

    @field_validator("quantiles")
    @classmethod
    def _check_q(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if value != (0.1, 0.5, 0.9):
            raise ValueError("calibration quantiles are frozen to [0.1, 0.5, 0.9]")
        return value


class WeightGrid(_Frozen):
    start: float
    stop: float
    step: float

    @model_validator(mode="after")
    def _check(self) -> WeightGrid:
        if (self.start, self.stop, self.step) != (0.0, 1.0, 0.05):
            raise ValueError("hybrid weight grid is frozen to 0.00, 0.05, ..., 1.00")
        return self

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(round(i * self.step, 2) for i in range(round(self.stop / self.step) + 1))


class HybridConfig(_Frozen):
    weight_grid: WeightGrid
    weight_fit: Literal["expanding_before_calibration_window"]
    horizon_24: Literal["previous_week_fallback_flagged"]


class SliceConfig(_Frozen):
    evening_civil_start_hours: tuple[int, ...]
    high_demand_training_quantile: float
    seasons_by_civil_month: dict[str, tuple[int, ...]]

    @model_validator(mode="after")
    def _check(self) -> SliceConfig:
        if self.high_demand_training_quantile != 0.9:
            raise ValueError("high-demand threshold is frozen to the training 90th percentile")
        if self.evening_civil_start_hours != (16, 17, 18, 19, 20):
            raise ValueError("evening slice is frozen to civil starts 16..20 ([16:00, 21:00))")
        months = sorted(m for ms in self.seasons_by_civil_month.values() for m in ms)
        if months != list(range(1, 13)):
            raise ValueError("seasons must partition the 12 months")
        return self


class BootstrapConfig(_Frozen):
    block: Literal["market_date"]
    replicates: int = Field(ge=200, le=100_000)
    seed: int
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _check_conf(cls, value: float) -> float:
        if value != 0.95:
            raise ValueError("bootstrap confidence is frozen to 0.95")
        return value


class BacktestConfig(_Frozen):
    backtest_version: Literal[1]
    inputs: BacktestInputs
    metrics: BacktestMetrics
    development: DevelopmentWindow
    holdout: HoldoutWindow
    training: TrainingPolicy
    calibration: CalibrationConfig
    hybrid: HybridConfig
    slices: SliceConfig
    bootstrap: BootstrapConfig


def load_backtest_config(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> BacktestConfig:
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/backtest.yaml")
    if not config_path.is_file():
        raise ConfigError(f"backtest config not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return BacktestConfig.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"backtest config is invalid:\n{exc}") from exc


class FeatureAllowlist(_Frozen):
    historical_demand: tuple[str, ...]
    availability_indicators: tuple[str, ...]
    horizon: tuple[Literal["horizon"]]
    market_calendar: tuple[str, ...]
    civil_calendar: tuple[str, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return (
            *self.historical_demand,
            *self.availability_indicators,
            *self.horizon,
            *self.market_calendar,
            *self.civil_calendar,
        )

    @model_validator(mode="after")
    def _check(self) -> FeatureAllowlist:
        cols = self.columns
        if len(set(cols)) != len(cols):
            raise ValueError("feature allowlist has duplicates")
        if any(
            c.startswith("target") or "missing_reason" in c or c.startswith("eligible")
            for c in cols
        ):
            raise ValueError("feature allowlist contains a target, lineage or eligibility column")
        if self.availability_indicators != ("lag_24h__available",):
            raise ValueError("only the registered lag_24h availability indicator is allowlisted")
        return self


class LightGBMParams(_Frozen):
    objective: Literal["quantile"]
    quantiles: tuple[float, ...]
    fixed_params: dict[str, float | int | bool]
    max_boosting_rounds: Literal[2000]
    early_stopping_rounds: Literal[100]
    candidates: dict[Literal["num_leaves", "min_data_in_leaf", "lambda_l2"], tuple[float, ...]]
    selection: dict[str, str]
    refit_rounds: str

    @model_validator(mode="after")
    def _check(self) -> LightGBMParams:
        if self.quantiles != (0.1, 0.5, 0.9):
            raise ValueError("LightGBM quantiles are frozen to 0.1, 0.5, 0.9")
        if not (
            self.fixed_params.get("deterministic") and self.fixed_params.get("seed") == 20261016
        ):
            raise ValueError("deterministic training with seed 20261016 is frozen")
        n = 1
        for values in self.candidates.values():
            n *= len(values)
        if n > 12:
            raise ValueError("at most 12 candidate configurations")
        return self

    @property
    def candidate_grid(self) -> list[dict[str, float | int]]:
        out: list[dict[str, float | int]] = []
        for leaves in self.candidates["num_leaves"]:
            for min_leaf in self.candidates["min_data_in_leaf"]:
                for l2 in self.candidates["lambda_l2"]:
                    out.append(
                        {
                            "num_leaves": int(leaves),
                            "min_data_in_leaf": int(min_leaf),
                            "lambda_l2": float(l2),
                        }
                    )
        return out


class NestedConfig(_Frozen):
    tuning_window_days: Literal[90]
    inner_maturity_gap_days: Literal[2]


class SeasonCalibrationConfig(_Frozen):
    seasons_by_month: Literal["inherited"]
    season_of_fold: str
    lookback_days: Literal[730]
    max_samples_per_horizon: Literal[270]
    min_samples_per_horizon: Literal[150]
    fallback: Literal["trailing"]


class Phase6CalibrationConfig(_Frozen):
    adjustment: Literal["quantile_specific_additive"]
    trailing: dict[Literal["window_days"], Literal[180]]
    season_matched: SeasonCalibrationConfig
    min_samples_per_horizon: Literal[150]
    quantile_interpolation: Literal["linear"]


class LightGBMProtocol(_Frozen):
    protocol_version: Literal[1]
    inherits: Literal["configs/backtest.yaml"]
    benchmark_tag: Literal["baseline-benchmark-v1"]
    benchmark_run_id: Literal["phase4_backtest_20261016_dev"]
    feature_allowlist: FeatureAllowlist
    training_rows: Literal["eligible_initial_ml_features_and_target_available"]
    nested: NestedConfig
    lightgbm: LightGBMParams
    calibration: Phase6CalibrationConfig
    candidates_compared: tuple[str, ...]
    ranking: dict[str, str]

    @model_validator(mode="after")
    def _check(self) -> LightGBMProtocol:
        expected = (
            "day_week_hybrid_frozen",
            "day_week_hybrid_season_calibrated",
            "lightgbm_raw",
            "lightgbm_trailing_calibrated",
            "lightgbm_season_calibrated",
        )
        if self.candidates_compared != expected:
            raise ValueError("compared candidates are frozen")
        if (
            self.ranking.get("point") != "mae_mw"
            or self.ranking.get("uncertainty") != "mean_pinball"
        ):
            raise ValueError("ranking metrics are frozen to mae_mw and mean_pinball")
        return self


def load_lightgbm_protocol(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> LightGBMProtocol:
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/lightgbm.yaml")
    if not config_path.is_file():
        raise ConfigError(f"LightGBM protocol not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return LightGBMProtocol.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"LightGBM protocol is invalid:\n{exc}") from exc


# --- Final holdout evaluation protocol (D-47) ---------------------------------------

_SHA256 = r"^[0-9a-f]{64}$"
_GIT_SHA = r"^[0-9a-f]{40}$"
_RUN_ID = r"^[A-Za-z0-9_.-]+$"
FINAL_REPORTED_METRICS = (
    "mae_mw",
    "rmse_mw",
    "wape",
    "mean_signed_error_mw",
    "pinball_p10",
    "pinball_p50",
    "pinball_p90",
    "mean_pinball",
    "coverage_p10_p90",
    "mean_interval_width_mw",
    "crossing_rate",
    "crossing_mean_magnitude_mw",
    "lower_tail_miss_rate",
    "upper_tail_miss_rate",
)
FINAL_SLICE_DIMENSIONS = (
    "origin_month",
    "horizon",
    "market_hour",
    "is_civil_evening",
    "day_type",
    "season",
    "is_high_demand",
)


class ArtefactModelSpec(_Frozen):
    file: str
    alpha: float
    iterations: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256)


class ArtefactFileSpec(_Frozen):
    file: str
    sha256: str = Field(pattern=_SHA256)
    rows: int | None = None


class HybridFallbackSpec(_Frozen):
    file: str
    sha256: str = Field(pattern=_SHA256)
    weight: float = Field(ge=0.0, le=1.0)


class TagSpec(_Frozen):
    tag_object: str = Field(pattern=_GIT_SHA)
    commit: str = Field(pattern=_GIT_SHA)


class RecordSpec(_Frozen):
    path: str
    sha256: str = Field(pattern=_SHA256)


class FinalArtefactSpec(_Frozen):
    run_id: str = Field(pattern=_RUN_ID)
    model_producing_commit: str = Field(pattern=_GIT_SHA)
    pre_evaluation_manifest_sha256: str = Field(pattern=_SHA256)
    candidate_record: RecordSpec
    models: dict[Literal["p10", "p50", "p90"], ArtefactModelSpec]
    trailing_offsets: ArtefactFileSpec
    hybrid_fallback: HybridFallbackSpec
    feature_allowlist_sha256: str = Field(pattern=_SHA256)
    lightgbm_version: str
    contract_tags: dict[str, TagSpec]


class FinalInputsSpec(_Frozen):
    feature_run_id: str = Field(pattern=_RUN_ID)
    feature_run_manifest_sha256: str = Field(pattern=_SHA256)
    profile: Literal["primary_operational"]
    files: tuple[RecordSpec, ...]


class EvaluationWindowSpec(_Frozen):
    first_origin: dt.date
    last_origin: dt.date
    expected_origins: int = Field(gt=0)
    expected_rows: int = Field(gt=0)
    horizons_per_origin: Literal[24] = 24

    @model_validator(mode="after")
    def _check(self) -> EvaluationWindowSpec:
        days = (self.last_origin - self.first_origin).days + 1
        if days != self.expected_origins:
            raise ValueError(f"window has {days} origins, protocol expects {self.expected_origins}")
        if self.expected_rows != self.expected_origins * self.horizons_per_origin:
            raise ValueError("expected_rows must equal expected_origins x horizons_per_origin")
        return self


class StagesSpec(_Frozen):
    prediction_columns_extra: tuple[str, ...]
    forbidden_in_prediction: tuple[str, ...]


class FinalMetricsSpec(_Frozen):
    primary: Literal["mae_mw"]
    secondary: Literal["wape_aggregate"]
    uncertainty_ranking: Literal["mean_pinball"]
    reported: tuple[str, ...]
    headline_rows: Literal["identical"]


class HighDemandSpec(_Frozen):
    rule: Literal["training_quantile"]
    training_last_origin: dt.date
    training_rows: int = Field(gt=0)
    threshold_mw: float = Field(gt=0)


class FinalSlicesSpec(_Frozen):
    dimensions: tuple[str, ...]
    expected_seasons: tuple[str, ...]
    high_demand: HighDemandSpec


class FinalBootstrapSpec(_Frozen):
    block: Literal["market_date"]
    replicates: Literal[2000]
    seed: Literal[20261016]
    confidence: float
    comparisons: tuple[Literal["mae_mw", "mean_pinball"], ...]

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: float) -> float:
        if value != 0.95:
            raise ValueError("bootstrap confidence is frozen to 0.95")
        return value


class FinalEvaluationProtocol(_Frozen):
    protocol_version: Literal[1]
    protocol_id: Literal["final-holdout-evaluation-v1"]
    decision_rule: Literal["descriptive_final"]
    selected_candidate: Literal["lightgbm_trailing_calibrated"]
    benchmark: Literal["day_week_hybrid_frozen"]
    headline_models: tuple[str, ...]
    quantile_order: Literal["frozen_uncorrected"]
    artefact: FinalArtefactSpec
    inputs: FinalInputsSpec
    holdout: EvaluationWindowSpec
    stages: StagesSpec
    metrics: FinalMetricsSpec
    slices: FinalSlicesSpec
    bootstrap: FinalBootstrapSpec
    rehearsal: EvaluationWindowSpec

    @model_validator(mode="after")
    def _check(self) -> FinalEvaluationProtocol:
        if self.headline_models != (self.selected_candidate, self.benchmark):
            raise ValueError("the headline comparison is frozen to the candidate and benchmark")
        if (self.holdout.first_origin, self.holdout.last_origin) != (
            HOLDOUT_FIRST_ORIGIN,
            dt.date(2026, 9, 30),
        ):
            raise ValueError("the holdout window is frozen to 2026-07-01 .. 2026-09-30")
        if self.rehearsal.last_origin >= self.holdout.first_origin:
            raise ValueError("rehearsal origins must precede the holdout")
        if self.slices.high_demand.training_last_origin != (
            self.holdout.first_origin - dt.timedelta(days=2)
        ):
            raise ValueError("high-demand training rows end at the last matured origin (H - 2 d)")
        if {k: m.alpha for k, m in self.artefact.models.items()} != {
            "p10": 0.1,
            "p50": 0.5,
            "p90": 0.9,
        }:
            raise ValueError("artefact models must be the p10/p50/p90 quantile models")
        if self.metrics.reported != FINAL_REPORTED_METRICS:
            raise ValueError("reported metrics are frozen")
        if self.slices.dimensions != FINAL_SLICE_DIMENSIONS:
            raise ValueError("slice dimensions are frozen")
        required = {"target_dispatch_totaldemand_hourly_mean_mw", "target_available"}
        if not required <= set(self.stages.forbidden_in_prediction):
            raise ValueError("target columns must be forbidden in the prediction stage")
        if self.bootstrap.comparisons != ("mae_mw", "mean_pinball"):
            raise ValueError("bootstrap comparisons are frozen to mae_mw and mean_pinball")
        return self


def load_final_evaluation_protocol(
    path: str | Path | None = None, paths: ProjectPaths | None = None
) -> FinalEvaluationProtocol:
    paths = paths or ProjectPaths.from_root()
    config_path = paths.root / (path or "configs/final_evaluation.yaml")
    if not config_path.is_file():
        raise ConfigError(f"final evaluation protocol not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return FinalEvaluationProtocol.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {config_path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"final evaluation protocol is invalid:\n{exc}") from exc
