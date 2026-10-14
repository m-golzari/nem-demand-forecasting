import importlib

import pytest

import nem_forecast

SUBPACKAGES = [
    "nem_forecast.ingestion",
    "nem_forecast.validation",
    "nem_forecast.features",
    "nem_forecast.models",
    "nem_forecast.evaluation",
    "nem_forecast.serving",
    "nem_forecast.monitoring",
    "nem_forecast.paths",
    "nem_forecast.config",
    "nem_forecast.contract",
    "nem_forecast.cli",
]


def test_version() -> None:
    assert nem_forecast.__version__ == "0.0.1"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(name)
