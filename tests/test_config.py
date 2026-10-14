import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pydantic
import pytest

from nem_forecast.config import ConfigError, load_forecast_config, parse_forecast_config
from nem_forecast.paths import ProjectPaths


def test_repository_config_is_valid() -> None:
    cfg = load_forecast_config()
    assert cfg.target.region == "VIC1"
    assert cfg.target.source_field == "TOTALDEMAND"
    assert cfg.target.unit == "MW"
    assert cfg.target.hourly_aggregation == "time_weighted_mean_mw"
    assert cfg.forecast.horizon_hours == 24
    assert cfg.forecast.origin_time == dt.time(0, 0)
    assert cfg.forecast.quantiles == (0.1, 0.5, 0.9)
    assert cfg.forecast.weather_in_primary_model is False
    assert cfg.time.market_timezone.utcoffset(None) == dt.timedelta(hours=10)
    assert cfg.history.start_date == dt.date(2022, 1, 1)
    assert cfg.history.end_date is None


@pytest.mark.parametrize(
    ("section", "key", "bad_value"),
    [
        ("target", "region", "NSW1"),
        ("target", "source_field", "OPERATIONAL_DEMAND"),
        ("target", "unit", "MWh"),
        ("target", "hourly_aggregation", "sum"),
        ("target", "native_interval_minutes", 30),
        ("time", "market_utc_offset_hours", 11),
        ("time", "interval_label", "end"),
        ("forecast", "origin_market_time", "06:00"),
        ("forecast", "horizon_hours", 48),
        ("forecast", "quantiles", [0.05, 0.5, 0.95]),
        ("forecast", "primary_feature_set", "demand_calendar_weather"),
        ("forecast", "weather_in_primary_model", True),
        ("history", "start_date", "2020-01-01"),
        ("quality", "min_hourly_interval_coverage", 0.0),
    ],
)
def test_frozen_contract_fields_reject_changes(
    valid_config_dict: dict[str, Any], section: str, key: str, bad_value: object
) -> None:
    data = copy.deepcopy(valid_config_dict)
    data[section][key] = bad_value
    with pytest.raises(ConfigError):
        parse_forecast_config(data)


def test_unknown_keys_rejected(valid_config_dict: dict[str, Any]) -> None:
    data = copy.deepcopy(valid_config_dict)
    data["forecast"]["use_observed_weather"] = True
    with pytest.raises(ConfigError):
        parse_forecast_config(data)


def test_missing_section_rejected(valid_config_dict: dict[str, Any]) -> None:
    data = copy.deepcopy(valid_config_dict)
    del data["quality"]
    with pytest.raises(ConfigError):
        parse_forecast_config(data)


def test_end_before_start_rejected(valid_config_dict: dict[str, Any]) -> None:
    data = copy.deepcopy(valid_config_dict)
    data["history"]["end_date"] = "2021-12-31"
    with pytest.raises(ConfigError):
        parse_forecast_config(data)


def test_explicit_end_date_accepted(valid_config_dict: dict[str, Any]) -> None:
    data = copy.deepcopy(valid_config_dict)
    data["history"]["end_date"] = "2026-09-30"
    assert parse_forecast_config(data).history.end_date == dt.date(2026, 9, 30)


def test_non_mapping_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_forecast_config(["not", "a", "mapping"])


def test_config_is_immutable() -> None:
    cfg = load_forecast_config()
    with pytest.raises(pydantic.ValidationError):
        cfg.target.region = "NSW1"  # type: ignore[misc]


def test_missing_file(tmp_project: ProjectPaths) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_forecast_config("configs/missing.yaml", tmp_project)


def test_invalid_yaml(tmp_project: ProjectPaths) -> None:
    bad: Path = tmp_project.configs / "bad.yaml"
    bad.write_text("target: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="parse"):
        load_forecast_config(bad, tmp_project)


def test_load_from_explicit_root(tmp_project: ProjectPaths) -> None:
    assert load_forecast_config(paths=tmp_project).target.region == "VIC1"
