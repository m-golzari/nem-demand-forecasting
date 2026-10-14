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
