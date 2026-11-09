from pathlib import Path

from typer.testing import CliRunner

from promo.cli import app

from .conftest import FIX

runner = CliRunner()


def test_inspect_and_estimate_run_on_the_fixture():
    r = runner.invoke(app, ["inspect", str(FIX)])
    assert r.exit_code == 0 and "coverage" in r.output
    r = runner.invoke(app, ["estimate", str(FIX)])
    assert r.exit_code == 0, r.output
    assert "Planned usage per stage" in r.output and "Daily quota check" in r.output


def test_estimate_rejects_a_bad_profile_map():
    r = runner.invoke(app, ["estimate", str(FIX), "--profile-map", "nope=gemini_lite"])
    assert r.exit_code == 2 and "unknown stage" in r.output


def test_stage_rejects_an_unknown_stage_name(tmp_path: Path):
    r = runner.invoke(app, ["stage", "nope", "--show", "x"])
    assert r.exit_code == 2
