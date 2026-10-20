import json

from cashcycle.cli import main


def test_demo_offline_runs(tmp_path, capsys):
    rc = main(["--offline", "--audit-dir", str(tmp_path), "demo", "--out", str(tmp_path / "report.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "£25,000 overdue + £1,800/month of quiet leaks = about 30 days of runway" in out
    rep = json.loads((tmp_path / "report.json").read_text())
    assert rep["hook"]["total_runway_days"] == 30.0
    assert main(["--audit-dir", str(tmp_path), "verify-log"]) == 0


def test_check_drafts_matches_expected(tmp_path):
    assert main(["--audit-dir", str(tmp_path), "check-drafts"]) == 0


def test_live_runway(tmp_path, capsys):
    assert main(["--live", "data/live/r3con-tech", "--audit-dir", str(tmp_path), "runway"]) == 0
    assert "seapoint_mcp" in capsys.readouterr().out
