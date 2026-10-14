"""Owner decision D-06: physical-run selection for intervention intervals."""

import datetime as dt

import pandas as pd
import pytest

from nem_forecast.ingestion.aemo_csv import parse_aemo_csv_text
from nem_forecast.ingestion.aemo_normalise import (
    ORDINARY,
    PHYSICAL,
    PHYSICAL_RUN,
    PRICING,
    PRICING_RUN,
    QUARANTINED,
    classify_primary_records,
    typed_frame,
)
from tests.aemo_fixtures import render, row

END = dt.datetime(2024, 6, 1, 12, 0)


def _classify(rows: list[list[str]]) -> pd.DataFrame:
    return classify_primary_records(typed_frame(parse_aemo_csv_text(render(rows))))


def test_documented_flag_values() -> None:
    # AEMO Intervention Pricing Methodology v2.3, s3.3
    assert (PHYSICAL_RUN, PRICING_RUN) == (1, 0)


def test_single_ordinary_record_retained() -> None:
    out = _classify([row(END)])
    assert out["selection_status"].tolist() == [ORDINARY]
    assert out["is_primary_target_record"].tolist() == [True]
    assert out["physical_minus_pricing_mw"].isna().all()


def test_physical_selected_and_difference_recorded() -> None:
    out = _classify(
        [row(END, intervention=0, demand="4000"), row(END, intervention=1, demand="4012.5")]
    )
    by_run = out.set_index("intervention")
    assert by_run.loc[1, "selection_status"] == PHYSICAL
    assert by_run.loc[0, "selection_status"] == PRICING
    assert out["is_primary_target_record"].sum() == 1  # never both in an hourly mean
    assert by_run.loc[1, "is_primary_target_record"]
    assert (out["physical_minus_pricing_mw"] == 12.5).all()


def test_pairs_are_per_region() -> None:
    rows = [row(END, intervention=0), row(END, intervention=1), row(END, region="NSW1")]
    out = _classify(rows)
    assert out.set_index("region_id").loc["NSW1", "selection_status"] == ORDINARY


@pytest.mark.parametrize(
    "rows",
    [
        [row(END, intervention=1)],  # physical without pricing counterpart
        [row(END), row(END)],  # two pricing-run rows
        [row(END, intervention=0), row(END, intervention=1), row(END, intervention=1)],  # > 2
        [row(END, intervention=2)],  # undocumented flag value
    ],
)
def test_unexpected_combinations_are_quarantined(rows: list[list[str]]) -> None:
    out = _classify(rows)
    assert (out["selection_status"] == QUARANTINED).all()
    assert not out["is_primary_target_record"].any()


def test_unexpected_runno_quarantined() -> None:
    bad = row(END)
    bad[1] = "2"
    assert _classify([bad])["selection_status"].tolist() == [QUARANTINED]
