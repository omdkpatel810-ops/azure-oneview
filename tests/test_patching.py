"""The patch compliance report: missing updates per server and per ring, before and after."""

from __future__ import annotations

import json

import pytest

from oneview import graph, patching
from tests.fixtures.graph import FakeAz

MACHINES = [
    {"name": "branch-01-winnipeg", "os": "linux", "ring": "test", "site": "winnipeg", "cloud": "onprem", "status": "Connected"},
    {"name": "branch-02-brandon", "os": "linux", "ring": "branch", "site": "brandon", "cloud": "onprem", "status": "Connected"},
    {"name": "branch-05-winkler", "os": "windows", "ring": "test", "site": "winkler", "cloud": "onprem", "status": "Connected"},
    {"name": "acquired-01", "os": "linux", "ring": "branch", "site": "aws-ca-central-1", "cloud": "aws", "status": "Connected"},
]


def assessment(machine, **counts):
    return {"machine": machine, "assessedAt": "2026-11-17T04:00:00Z", "status": "Succeeded", "counts": counts}


BEFORE = [
    assessment("branch-01-winnipeg", critical=2, security=31, other=40),
    assessment("branch-02-brandon", critical=0, security=12, other=5),
    assessment("BRANCH-05-WINKLER", Critical=1, Security=3, UpdateRollup=1, Definition=2),
    # acquired-01 has never been assessed
]
AFTER = [
    assessment("branch-01-winnipeg", critical=0, security=0, other=40),
    assessment("branch-02-brandon", critical=0, security=0, other=5),
    assessment("branch-05-winkler", Critical=0, Security=0, Definition=1),
    assessment("acquired-01", critical=0, security=4, other=9),
]


def az(assessments):
    return FakeAz({'hybridcompute/machines"': MACHINES, "patchassessmentresources": assessments})


def test_queries_are_for_one_resource_group():
    text = graph.kql("patch-assessment", "rg-oneview-prod")
    assert 'resourceGroup =~ "rg-oneview-prod"' in text and "{resource_group}" not in text
    with pytest.raises(ValueError, match="not a resource group name"):
        graph.kql("machines", 'x" or 1==1 //')


def test_graph_failures_are_reported():
    with pytest.raises(RuntimeError, match="Resource Graph query failed: ERROR: graph went wrong"):
        graph.query(FakeAz(fail="graph"), "resources")
    with pytest.raises(RuntimeError, match="az account show"):
        graph.az_json(FakeAz(fail="account"), ["account", "show"])


def test_graph_accepts_a_plain_list_too():
    def shell(args, stdin):
        return 0, json.dumps([{"name": "a"}, "noise"])

    assert graph.query(shell, "resources") == [{"name": "a"}]


def test_a_snapshot_counts_missing_updates_per_server():
    snap = patching.snapshot(az(BEFORE), "rg-oneview-prod")
    servers = {s["name"]: s for s in snap["servers"]}
    assert servers["branch-01-winnipeg"] | {} == {
        "name": "branch-01-winnipeg", "os": "linux", "ring": "test", "site": "winnipeg",
        "critical": 2, "security": 31, "other": 40, "assessed_at": "2026-11-17T04:00:00Z",
    }  # fmt: skip
    # Classification names differ by operating system and in case; Windows' extras count as "other".
    assert (servers["branch-05-winkler"]["critical"], servers["branch-05-winkler"]["other"]) == (1, 3)
    assert servers["acquired-01"]["critical"] is None


def test_never_assessed_is_not_compliant():
    s = patching.summary(patching.snapshot(az(BEFORE), "rg-oneview-prod"))
    assert s == {
        "servers": 4,
        "compliant": 0,
        "compliance": 0.0,
        "missing_critical": 3,
        "missing_security": 46,
        "never_assessed": ["acquired-01"],
        "by_ring": {"branch": "0 of 2", "test": "0 of 2"},
    }


def test_after_patching():
    s = patching.summary(patching.snapshot(az(AFTER), "rg-oneview-prod"))
    assert (s["compliant"], s["compliance"], s["by_ring"]) == (3, 75.0, {"branch": "1 of 2", "test": "2 of 2"})
    assert patching.summary({"servers": []})["compliance"] == 0.0


def test_the_report():
    text = patching.to_markdown(patching.snapshot(az(BEFORE), "rg-oneview-prod"))
    assert text.startswith("**Patch compliance: 0 of 4 servers (0%)**, 3 critical and 46 security updates missing.")
    assert "branch ring: 0 of 2 compliant, test ring: 0 of 2 compliant." in text
    assert "| branch-01-winnipeg | test | linux | 2 | 31 | 40 | 2026-11-17T04:00:00Z |" in text
    assert "| acquired-01 | branch | linux | - | - | - | never |" in text


def test_before_and_after():
    before = patching.snapshot(az(BEFORE), "rg-oneview-prod")
    after = patching.snapshot(az(AFTER), "rg-oneview-prod")
    after["servers"].append({**after["servers"][0], "name": "branch-09-new", "critical": None, "security": None})
    text = patching.compare(before, after)
    assert text.startswith("**Patch compliance: 0% before, 60% after.** ")
    assert "Missing critical and security updates: 49 before, 4 after." in text
    assert "| branch-01-winnipeg | 33 | 0 |" in text
    assert "| acquired-01 | - | 4 |" in text
    assert "| branch-09-new | - | - |" in text


def test_assess_asks_every_server():
    fake = FakeAz()
    patching.assess(fake, "rg-oneview-prod", ["branch-01-winnipeg", "acquired-01"])
    posts = [c for c in fake.calls if c[1] == "rest"]
    assert len(posts) == 2
    assert posts[0][-1] == (
        "https://management.azure.com/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-oneview-prod"
        "/providers/Microsoft.HybridCompute/machines/branch-01-winnipeg/assessPatches?api-version=2024-07-10"
    )


def test_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(patching, "RESULTS", tmp_path)
    assert patching.main(["snapshot", "--save", "before"], shell=az(BEFORE)) == 0
    out = capsys.readouterr().out
    assert "0 of 4 servers" in out and f"Saved {tmp_path / 'patches-before.json'}" in out
    assert patching.main(["snapshot", "--save", "after"], shell=az(AFTER)) == 0
    assert patching.main(["snapshot"], shell=az(AFTER)) == 0
    assert "Saved" not in capsys.readouterr().out.split("3 of 4")[-1]
    assert patching.main(["compare", str(tmp_path / "patches-before.json"), str(tmp_path / "patches-after.json")]) == 0
    assert "0% before, 75% after" in capsys.readouterr().out

    fake = az(BEFORE)
    assert patching.main(["snapshot", "--assess"], shell=fake) == 0
    assert "Asked 4 servers to check for updates" in capsys.readouterr().out
    assert sum(c[1] == "rest" for c in fake.calls) == 4
