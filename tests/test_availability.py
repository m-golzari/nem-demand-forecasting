"""Owner decision D-07 availability profiles and A-01 endpoint normalisation."""

import datetime as dt
from pathlib import Path

import pytest

from nem_forecast.config import ConfigError, load_feature_availability_config
from nem_forecast.contract import (
    NEM_MARKET_TZ,
    canonical_hour_start,
    latest_usable_interval_end,
    parse_settlementdate,
)
from nem_forecast.paths import ProjectPaths
from tests.conftest import REPO_ROOT

ORIGIN = dt.datetime(2024, 4, 4, 0, 0, tzinfo=NEM_MARKET_TZ)


def test_primary_profile_is_one_hour_operational() -> None:
    cfg = load_feature_availability_config()
    assert cfg.primary_operational.availability_lag_hours == 1
    assert cfg.primary_operational.experiment_type == "operational"
    assert "modelling assumption" in cfg.primary_operational.label
    assert latest_usable_interval_end(ORIGIN, 1) == ORIGIN - dt.timedelta(hours=1)


def test_zero_lag_is_labelled_sensitivity_only() -> None:
    cfg = load_feature_availability_config()
    assert cfg.sensitivity_zero_lag.availability_lag_hours == 0
    assert cfg.sensitivity_zero_lag.experiment_type == "sensitivity"
    assert cfg.sensitivity_zero_lag.label.startswith("SENSITIVITY ONLY")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("availability_lag_hours: 1", "availability_lag_hours: 0"),  # primary cannot be zero-lag
        ("experiment_type: sensitivity", "experiment_type: operational"),
        ('label: "SENSITIVITY ONLY', 'label: "'),
    ],
)
def test_profiles_cannot_be_confused(tmp_project: ProjectPaths, old: str, new: str) -> None:
    text = (REPO_ROOT / "configs" / "feature_availability.yaml").read_text(encoding="utf-8")
    assert old in text
    path = Path(tmp_project.configs / "feature_availability.yaml")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_feature_availability_config(paths=tmp_project)


def test_primary_buffer_excludes_final_hour_before_origin() -> None:
    """With the 1 h buffer, the record ending exactly at origin - 1 h is the last usable."""
    cutoff = latest_usable_interval_end(ORIGIN, 1)
    assert parse_settlementdate("2024/04/03 23:00:00") <= cutoff
    assert parse_settlementdate("2024/04/03 23:05:00") > cutoff
    assert latest_usable_interval_end(ORIGIN, 0) == ORIGIN


def test_availability_rejects_naive_or_negative() -> None:
    with pytest.raises(ValueError):
        latest_usable_interval_end(dt.datetime(2024, 4, 4), 1)
    with pytest.raises(ValueError):
        latest_usable_interval_end(ORIGIN, -1)


def test_endpoint_normalisation_boundaries() -> None:
    """Source support (00:00, 01:00] -> canonical [00:00, 01:00) labelled 00:00."""
    hour = dt.datetime(2024, 4, 4, 0, 0, tzinfo=NEM_MARKET_TZ)
    included = ["2024/04/04 00:05:00", "2024/04/04 01:00:00"]  # open left end, closed right end
    excluded = {
        "2024/04/04 00:00:00": hour - dt.timedelta(hours=1),
        "2024/04/04 01:05:00": hour + dt.timedelta(hours=1),
    }
    for stamp in included:
        assert canonical_hour_start(parse_settlementdate(stamp)) == hour
    for stamp, other in excluded.items():
        assert canonical_hour_start(parse_settlementdate(stamp)) == other
    # the canonical label equals the left endpoint of the half-open window, not a source stamp
    assert hour not in {parse_settlementdate(s) for s in included}
