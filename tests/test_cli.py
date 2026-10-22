"""The CLI through its entry point: an error the user can act on is an exit code, never a traceback."""

import json

from typer.testing import CliRunner

from livery import cli
from tests.conftest import OWNER, checked, run_cli, write_design

STEADY = 'def build(kit, p):\n    print("building")\n    return Livery(base="#3060c0")\n'
PRINTS = 'print("loading")\n\n\n' + STEADY
SYNTAX_ERROR = "def build(kit, p)\n    return None\n"


def test_help_runs():
    assert CliRunner().invoke(cli.app, ["--help"]).exit_code == 0


def test_help_names_the_owner_from_the_settings():
    assert OWNER in CliRunner().invoke(cli.app, ["install", "--help"]).output


def test_config_show_prints_the_settings(monkeypatch, ws, capsys):
    assert run_cli(monkeypatch, ws, "config", "show", "--json") == 0
    assert json.loads(capsys.readouterr().out)["owner"] == OWNER


def test_an_unknown_design_is_a_usage_error(monkeypatch, ws):
    for command in ("check", "build", "lint"):
        assert run_cli(monkeypatch, ws, command, "nope") == 2


def test_a_broken_script_fails_check_and_build(monkeypatch, ws):
    write_design(ws, "broken", SYNTAX_ERROR)
    assert run_cli(monkeypatch, ws, "check", "broken") == 1
    assert run_cli(monkeypatch, ws, "build", "broken") == 1
    assert run_cli(monkeypatch, ws, "lint", "broken") == 1


def test_bad_params_are_usage_errors(monkeypatch, ws):
    write_design(ws, "d", STEADY)
    assert run_cli(monkeypatch, ws, "lint", "d", "--set", "nope=1") == 2
    assert run_cli(monkeypatch, ws, "sheet", "d", "--wedge", "width") == 2


def test_check_prints_only_its_report_on_stdout_when_the_design_prints(monkeypatch, ws, capsys):
    write_design(ws, "d", PRINTS)
    assert run_cli(monkeypatch, ws, "check", "d") == 0
    assert json.loads(capsys.readouterr().out)["passed"]


def test_a_verdict_that_is_not_json_is_refused(monkeypatch, ws, tmp_path):
    write_design(ws, "d", STEADY)
    verdict = tmp_path / "verdict.json"
    verdict.write_text("{not json", encoding="utf-8")
    assert run_cli(monkeypatch, ws, "critic", "record", "d", str(verdict)) == 1
    assert run_cli(monkeypatch, ws, "critic", "record", "d", str(tmp_path / "missing.json")) == 2


def test_build_all_fails_on_a_broken_fixture_but_not_on_an_unapproved_livery(monkeypatch, ws):
    checked(ws, "new", STEADY, render=True)
    assert run_cli(monkeypatch, ws, "build", "--all") == 0
    write_design(ws, "broken", 'ROLE = "fixture"\n' + SYNTAX_ERROR)
    assert run_cli(monkeypatch, ws, "build", "--all") == 1
    report = json.loads((ws.root / ".cache" / "build-all.json").read_text(encoding="utf-8"))
    assert report["broken"]["status"] == "failed"
