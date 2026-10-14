"""Live NEMWeb checks. Deselected by default; run with ``pytest -m network``."""

import datetime as dt

import pytest

from nem_forecast.config import load_aemo_source_config
from nem_forecast.ingestion import nemweb
from nem_forecast.paths import ProjectPaths

pytestmark = pytest.mark.network


def test_live_listing_resolves_april_2024_archive() -> None:
    cfg = load_aemo_source_config()
    paths = ProjectPaths.from_root()
    with nemweb.make_client(cfg) as client:
        planned = nemweb.plan_download(
            cfg,
            paths,
            dt.date(2024, 4, 4),
            dt.date(2024, 4, 11),
            lambda url: nemweb.fetch_with_retries(client, url, cfg).text,
        )
    assert [p.name for p in planned] == ["PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip"]
    assert planned[0].listed_bytes > 1_000_000
