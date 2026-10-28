from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from roundtrip import __version__
from roundtrip.cli import app
from roundtrip.config import Settings

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_process_month_and_discover(settings: Settings, clean_month: tuple[str, Path]) -> None:
    month, _ = clean_month
    result = runner.invoke(app, ["process-month", "--month", month])
    assert result.exit_code == 0, result.output
    assert f"{month}: processed" in result.stdout
    assert "validation: pass" in result.stdout

    result = runner.invoke(app, ["discover", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["unchanged"] == [month]


def test_process_month_rejects_bad_month(settings: Settings) -> None:
    result = runner.invoke(app, ["process-month", "--month", "2024-13"])
    assert result.exit_code != 0


def test_phase_2_commands_say_so(settings: Settings) -> None:
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 3
    assert "phase 2" in result.output


def test_run_causal_without_panel_fails_clearly(settings: Settings) -> None:
    result = runner.invoke(app, ["run-causal", "--quick"])
    assert result.exit_code != 0
