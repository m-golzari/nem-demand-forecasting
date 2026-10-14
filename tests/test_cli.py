import json
from pathlib import Path

from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.paths import ProjectPaths

runner = CliRunner()


def test_validate_config_ok() -> None:
    result = runner.invoke(app, ["validate-config"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("OK:")
    assert "VIC1 TOTALDEMAND hourly mean MW" in result.output


def test_show_config_prints_json() -> None:
    result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["config"]["target"]["region"] == "VIC1"
    assert payload["config"]["forecast"]["horizon_hours"] == 24
    assert Path(payload["project_root"]).is_absolute()


def test_invalid_config_exits_nonzero(tmp_project: ProjectPaths) -> None:
    cfg_path = tmp_project.default_forecast_config
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace("region: VIC1", "region: NSW1"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate-config", "--root", str(tmp_project.root)])
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_invalid_root_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["show-config", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "ERROR" in result.output
