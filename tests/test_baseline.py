"""The security baseline report: rule scores per server, Defender findings, before and after."""

from __future__ import annotations

import pytest

from oneview import baseline, graph
from tests.fixtures.graph import FakeAz
from tests.test_patching import MACHINES


def rule(name, ok, reason=""):
    return {"resourceId": name, "complianceStatus": ok, "reasons": [{"code": "x", "phrase": reason}] if reason else []}


def report(machine, os, *rules, status="NonCompliant"):
    name = "AzureLinuxBaseline" if os == "linux" else "AzureWindowsBaseline"
    return {"machine": machine, "baseline": name, "status": status, "checkedAt": "2026-11-18T15:00:00Z", "rules": list(rules)}


SSH_ROOT = "Ensure SSH root login is disabled"
SSH_PASSWORDS = "Ensure SSH password authentication is disabled"
SHADOW = "Ensure permissions on /etc/shadow are configured"
BLANK = "Accounts: Limit local account use of blank passwords to console logon only"

BEFORE = [
    report(
        "branch-01-winnipeg",
        "linux",
        rule(SSH_ROOT, False, "PermitRootLogin is yes"),
        rule(SSH_PASSWORDS, False, "PasswordAuthentication is yes"),
        rule(SHADOW, False, "/etc/shadow mode is 644"),
        rule("Ensure the audit daemon is installed", True),
    ),
    report("branch-02-brandon", "linux", rule(SSH_ROOT, "false"), rule(SHADOW, "true")),
    report(
        "branch-05-winkler",
        "windows",
        rule(BLANK, False, "  "),
        rule("Minimum password length", "false", "0 characters"),
        *[rule(f"Windows rule {i}", True) for i in range(6)],
        rule("A rule with no name", False) | {"resourceId": None},
    ),
    # acquired-01 hasn't reported yet
]
AFTER = [
    report(
        "branch-01-winnipeg",
        "linux",
        *[rule(r, True) for r in (SSH_ROOT, SSH_PASSWORDS, SHADOW)],
        rule("Ensure the audit daemon is installed", "Compliant"),
        status="Compliant",
    ),
    report("branch-02-brandon", "linux", rule(SSH_ROOT, True), rule(SHADOW, True), rule("Ensure cron is restricted", False)),
    report(
        "branch-05-winkler",
        "windows",
        rule(BLANK, True),
        rule("Minimum password length", True),
        *[rule(f"Windows rule {i}", True) for i in range(6)],
        status="Compliant",
    ),
    report("acquired-01", "linux", rule(SSH_ROOT, True), rule(SHADOW, False)),
]
FINDINGS = [
    {"machine": "branch-01-winnipeg", "recommendation": "Vulnerability findings should be resolved", "severity": "High"},
    {"machine": "branch-01-winnipeg", "recommendation": "System updates should be installed", "severity": "Medium"},
    {"machine": "BRANCH-01-WINNIPEG", "recommendation": "Something new", "severity": None},
]


def az(reports, findings=()):
    tables = {'hybridcompute/machines"': MACHINES, "guestconfigurationresources": reports, "securityresources": list(findings)}
    return FakeAz(tables)


def test_the_queries():
    assert 'resourceGroup =~ "rg-oneview-prod"' in graph.kql("baseline", "rg-oneview-prod")
    assert '/resourcegroups/rg-oneview-prod/"' in graph.kql("defender", "rg-oneview-prod")


def test_scores_per_server():
    fleet = {s.name: s for s in baseline.load(baseline.snapshot(az(BEFORE, FINDINGS), "rg-oneview-prod"))}
    winnipeg = fleet["branch-01-winnipeg"]
    assert (winnipeg.passed, winnipeg.total, winnipeg.score) == (1, 4, 25.0)
    assert winnipeg.failed == [
        "Ensure SSH password authentication is disabled: PasswordAuthentication is yes",
        "Ensure SSH root login is disabled: PermitRootLogin is yes",
        "Ensure permissions on /etc/shadow are configured: /etc/shadow mode is 644",
    ]
    assert winnipeg.findings == {"High": 1, "Medium": 1, "Unknown": 1}
    assert fleet["branch-02-brandon"].score == 50.0  # "true" and "false" as strings work too
    # A blank reason shows the rule alone; a rule without a name still counts.
    assert fleet["branch-05-winkler"].failed[0] == BLANK and "unnamed rule" in fleet["branch-05-winkler"].failed
    assert fleet["acquired-01"].baseline is None and fleet["acquired-01"].score is None


def test_the_summary():
    assert baseline.summary(baseline.snapshot(az(BEFORE, FINDINGS), "rg-oneview-prod")) == {
        "servers": 4,
        "reporting": 3,
        "average_score": 47.2,  # (25 + 50 + 66.7) / 3
        "rules_failed": 7,
        "fully_compliant": 0,
        "no_report": ["acquired-01"],
        "defender_findings": 3,
    }
    assert baseline.summary({"servers": []})["average_score"] is None


def test_the_report():
    text = baseline.to_markdown(baseline.snapshot(az(BEFORE, FINDINGS), "rg-oneview-prod"), worst=2)
    assert text.startswith("**Security baseline: 47.2% of rules passed on average, across 3 of 4 servers reporting.**")
    assert "7 failing rules; 0 servers pass every rule; 3 Defender for Cloud findings." in text
    assert "| branch-01-winnipeg | AzureLinuxBaseline | 25% | 3 of 4 | 1 high, 1 medium, 1 unknown |" in text
    assert "| acquired-01 | no report yet | - | 0 of 0 | none |" in text
    assert "**branch-01-winnipeg** fails:\n- Ensure SSH password authentication is disabled: Password" in text
    assert "- and 1 more" in text


def test_before_and_after():
    before = baseline.snapshot(az(BEFORE), "rg-oneview-prod")
    after = baseline.snapshot(az(AFTER), "rg-oneview-prod")
    text = baseline.compare(before, after)
    assert text.startswith("**Security baseline: 47.2% before, 79.2% after.** Failing rules: 7 before, 2 after.")
    assert "| branch-01-winnipeg | 25% | 100% | 3 | 0 |" in text
    assert "| branch-02-brandon | 50% | 66.7% | 1 | 1 |" in text
    assert "| branch-05-winkler | 66.7% | 100% | 3 | 0 |" in text
    assert "| acquired-01 | - | 50% | 0 | 1 |" in text
    after["servers"].append({**after["servers"][0], "name": "branch-09-new"})
    assert "| branch-09-new | - | 50% | 0 | 1 |" in baseline.compare(before, after)  # a copy of acquired-01


def test_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(baseline, "RESULTS", tmp_path)
    assert baseline.main(["snapshot", "--save", "before"], shell=az(BEFORE)) == 0
    assert f"Saved {tmp_path / 'baseline-before.json'}" in capsys.readouterr().out
    assert baseline.main(["snapshot", "--save", "after"], shell=az(AFTER)) == 0
    assert baseline.main(["snapshot"], shell=az(AFTER)) == 0
    assert "Saved" not in capsys.readouterr().out.split("2 failing rules")[-1]
    assert baseline.main(["compare", str(tmp_path / "baseline-before.json"), str(tmp_path / "baseline-after.json")]) == 0
    assert "47.2% before, 79.2% after" in capsys.readouterr().out


@pytest.mark.parametrize("status", [True, "true", "True", "Compliant"])
def test_passing_is_spelled_several_ways(status):
    assert baseline._passes({"complianceStatus": status})
