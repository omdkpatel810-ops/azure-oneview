"""The emergency patch drill, run against a simulated fleet and clock: rings in order, holding the
branches when the test ring fails, and hours to patch measured to the first clean check."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from oneview import drill
from tests.fixtures.graph import FakeAz

START = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
FLEET = [  # name, os, ring, missing critical+security at the start, minutes from install to clean
    ("branch-01-winnipeg", "linux", "test", 33, 40),
    ("branch-05-winkler", "windows", "test", 4, 70),
    ("acquired-02", "linux", "test", 0, 10),
    ("branch-02-brandon", "linux", "branch", 12, 35),
    ("branch-06-selkirk", "windows", "branch", 6, 95),
]


class Lab:
    """Servers that come clean a set time after being told to install, on a clock the drill moves."""

    def __init__(self, fleet=FLEET, never=(), fail=None):
        self.clock = START
        self.fleet = {name: (os, ring, missing, minutes) for name, os, ring, missing, minutes in fleet}
        self.never = set(never)  # servers that never come clean
        self.installed: dict[str, datetime] = {}
        self.az = FakeAz({'hybridcompute/machines"': self.machines, "patchassessmentresources": self.assessments}, fail=fail)

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def shell(self, args, stdin=None):
        code, out = self.az(args, stdin)
        url = args[args.index("--url") + 1] if "--url" in args else ""
        if code == 0 and url and "/installPatches" in url:
            self.installed[url.split("/machines/")[1].split("/")[0]] = self.clock
        return code, out

    def machines(self):
        return [{"name": n, "os": os, "ring": ring, "site": "x", "cloud": "onprem"} for n, (os, ring, *_) in self.fleet.items()]

    def assessments(self):
        rows = []
        for name, (_os, _ring, missing, minutes) in self.fleet.items():
            done_at = self.installed.get(name, START + timedelta(days=99)) + timedelta(minutes=minutes)
            if name not in self.never and self.clock >= done_at:
                rows.append({"machine": name, "assessedAt": self.clock.isoformat(), "counts": {"critical": 0, "security": 0}})
            else:
                yesterday = (START - timedelta(days=1)).isoformat()
                counts = {"critical": min(missing, 1), "security": max(missing - 1, 0)}
                rows.append({"machine": name, "assessedAt": yesterday, "counts": counts})
        return rows

    def run(self, **kw):
        return drill.Drill(self.shell, "rg-oneview-prod", now=self.now, sleep=self.sleep).run(["test", "branch"], **kw)


def test_both_rings_in_order():
    lab = Lab()
    result = lab.run(poll_minutes=5)
    servers = {s["name"]: s for s in result["servers"]}
    assert all(s["status"] == "patched" for s in servers.values()) and result["held"] == []
    # The test ring is done when its slowest server is (70 minutes); only then do the branches start.
    assert result["rings"] == {"test": 1.17, "branch": 1.58}
    assert min(t for n, t in lab.installed.items() if servers[n]["ring"] == "branch") == START + timedelta(minutes=70)
    assert servers["acquired-02"]["hours"] == 0.17  # nothing was missing, but it still needs a fresh clean check
    assert servers["branch-06-selkirk"]["hours"] == 2.75  # 70 minutes waiting for the test ring, then 95
    assert [s["name"] for s in result["servers"]][:3] == ["acquired-02", "branch-01-winnipeg", "branch-05-winkler"]


def test_the_install_request():
    lab = Lab()
    lab.run(timeout_hours=3)
    post = next(c for c in lab.az.calls if "--url" in c and "installPatches" in c[c.index("--url") + 1])
    assert post[post.index("--url") + 1] == (
        "https://management.azure.com/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-oneview-prod"
        "/providers/Microsoft.HybridCompute/machines/acquired-02/installPatches?api-version=2024-07-10"
    )
    assert json.loads(post[post.index("--body") + 1]) == {
        "maximumDuration": "PT3H",
        "rebootSetting": "IfRequired",
        "linuxParameters": {"classificationsToInclude": ["Critical", "Security"]},
        "windowsParameters": {"classificationsToInclude": ["Critical", "Security"]},
    }
    assert sum(c[:3] == ["az", "account", "show"] for c in lab.az.calls) == 1


def test_a_stuck_test_server_holds_the_branches_back():
    lab = Lab(never={"branch-05-winkler"})
    result = lab.run(poll_minutes=10, timeout_hours=2)
    servers = {s["name"]: s for s in result["servers"]}
    assert servers["branch-05-winkler"]["status"] == "not done after 2 hours"
    assert result["held"] == ["branch"] and "branch-02-brandon" not in servers
    assert not any(n.startswith(("branch-02", "branch-06")) for n in lab.installed)


def test_a_failed_install_holds_the_branches_back_too():
    lab = Lab(fail="installPatches")
    result = lab.run()
    assert all(s["status"].startswith("install failed: ") for s in result["servers"]) and result["held"] == ["branch"]


def test_checks_that_fail_while_a_server_is_busy_are_retried():
    lab = Lab(fail="assessPatches")
    assert all(s["status"] == "patched" for s in lab.run()["servers"])


def test_an_unreadable_check_time_counts_as_now():
    lab = Lab(fleet=FLEET[:1])
    original = lab.assessments
    lab.az.tables["patchassessmentresources"] = lambda: [r | {"assessedAt": "soon"} for r in original()]
    result = drill.Drill(lab.shell, "rg-oneview-prod", now=lab.now, sleep=lab.sleep).run(["test"], poll_minutes=5)
    assert result["servers"][0]["hours"] == 0.67  # the check at 40 minutes


def test_rings_with_no_servers_are_skipped():
    lab = Lab(fleet=[f for f in FLEET if f[2] == "branch"])
    result = lab.run()
    assert list(result["rings"]) == ["branch"]


def test_the_report():
    text = drill.to_markdown(Lab().run())
    assert text.startswith(
        "**Emergency patch drill: 5 of 5 servers patched, the last 2.75 hours after the start.** "
        "Test ring took 1.17 h, branch ring took 1.58 h."
    )
    assert "| branch-01-winnipeg | test | 33 | 0.67 | patched |" in text
    held = drill.to_markdown(Lab(fail="installPatches").run())
    assert held.startswith("**Emergency patch drill: 0 of 3 servers patched.**")
    assert "Held back: the branch ring, because the ring before it didn't finish." in held
    assert (
        "| acquired-02 | test | 0 | - | install failed: az rest --method post failed: ERROR: installPatches went wrong |" in held
    )
    empty = drill.to_markdown({"servers": [], "rings": {}, "held": []})
    assert empty.startswith("**Emergency patch drill: 0 of 0 servers patched.**\n")


def test_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(drill, "RESULTS", tmp_path)
    lab = Lab()
    assert drill.main([], shell=lab.shell, now=lab.now, sleep=lab.sleep) == 0
    out = capsys.readouterr().out
    assert "- test ring (3): acquired-02, branch-01-winnipeg, branch-05-winkler" in out and "--yes" in out
    assert lab.installed == {}  # nothing happens without --yes
    assert drill.main(["--yes"], shell=lab.shell, now=lab.now, sleep=lab.sleep) == 0
    out = capsys.readouterr().out
    assert "5 of 5 servers patched" in out and "Saved" in out
    saved = json.loads((tmp_path / "drill-2026-11-17T0300.json").read_text())
    assert len(saved["servers"]) == 5

    stuck = Lab(never={"acquired-02"})
    assert drill.main(["--yes", "--timeout-hours", "1"], shell=stuck.shell, now=stuck.now, sleep=stuck.sleep) == 1
    branch_only = Lab()
    assert drill.main(["--rings", "branch"], shell=branch_only.shell) == 0
    assert "- branch ring (2): branch-02-brandon, branch-06-selkirk" in capsys.readouterr().out
