"""onboarding/onboard.py against a stand-in shell: the scripts it sends, where the secret goes (and
doesn't), skipping machines that are already connected, and failures."""

from __future__ import annotations

import base64
import json

import pytest

from onboarding import onboard
from onboarding.onboard import connect_settings, fetch_secret, is_connected, load_inventory, resolve_hosts, script_for

SECRET = "Sup3r~Secret.Value_123"
SETTINGS = {
    "tenant_id": "tenant-guid",
    "subscription_id": "sub-guid",
    "resource_group": "rg-oneview-prod",
    "location": "canadacentral",
    "client_id": "client-guid",
    "key_vault": "kv-oneview-1234",
    "secret_name": "arc-onboarding-secret",
}


class FakeShell:
    def __init__(self, connected=(), failing=(), settings=None, aws=None, codes=None):
        self.calls: list[tuple[list[str], str | None]] = []
        self.connected, self.failing = set(connected), set(failing)
        self.settings = SETTINGS if settings is None else settings
        self.aws = {"acquired-01": "198.51.100.10", "acquired-02": "198.51.100.11"} if aws is None else aws
        self.codes = codes or {}

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        text = " ".join(args)
        for key, code in self.codes.items():
            if key in text:
                return code, ""
        if "output -json public_ips" in text:
            return 0, json.dumps(self.aws)
        if "output -json connect" in text:
            return 0, json.dumps(self.settings)
        if text.startswith("az keyvault secret show"):
            return 0, SECRET + "\n"
        host = next(a for a in args if "@" in a).split("@")[1]
        name = next((n for n, h in HOSTS.items() if h == host), host)
        if "azcmagent show" in text or (stdin and "show" in stdin and "connect" not in stdin):
            status = "Connected" if name in self.connected else "Disconnected"
            return 0, f"Resource Name : {name}\nAgent Status : {status}\n"
        if name in self.failing:
            return 1, f"FATAL: AADSTS7000215: Invalid client secret provided: {SECRET}"
        return 0, "Connected machine to Azure"


MACHINES = load_inventory()
HOSTS = {m.name: m.host for m in MACHINES} | {"acquired-01": "198.51.100.10", "acquired-02": "198.51.100.11"}


def machine(name):
    return next(m for m in resolve_hosts(MACHINES, FakeShell()) if m.name == name)


# --- The inventory -----------------------------------------------------------------------------------


def test_the_lab_has_six_branches_and_two_acquired_servers():
    assert len(MACHINES) == 8
    assert sum(m.cloud == "onprem" for m in MACHINES) == 6 and sum(m.cloud == "aws" for m in MACHINES) == 2
    assert {m.os for m in MACHINES} == {"linux", "windows"}
    assert {m.name for m in MACHINES if m.ring == "test"} == {"branch-01-winnipeg", "branch-05-winkler", "acquired-02"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"os": "macos"}, "os is linux or windows"),
        ({"ring": "prod"}, "ring is test or branch"),
        ({"cloud": "gcp"}, "cloud is onprem or aws"),
        ({"name": "Branch 1"}, "names are 3 to 41 lowercase letters"),
    ],
)
def test_bad_inventories(tmp_path, change, message):
    data = json.loads(onboard.INVENTORY.read_text())
    data["machines"][0].update(change)
    (tmp_path / "i.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        load_inventory(tmp_path / "i.json")


def test_inventories_need_unique_names_and_a_test_ring(tmp_path):
    data = json.loads(onboard.INVENTORY.read_text())
    data["machines"][1]["name"] = data["machines"][0]["name"]
    for m in data["machines"]:
        m["ring"] = "branch"
    (tmp_path / "i.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"unique.*test ring"):
        load_inventory(tmp_path / "i.json")


# --- Hosts, settings and the secret -------------------------------------------------------------------


def test_aws_hosts_come_from_lab_aws():
    resolved = resolve_hosts(MACHINES, FakeShell())
    assert [m.host for m in resolved if m.cloud == "aws"] == ["198.51.100.10", "198.51.100.11"]
    assert [m.host for m in resolved if m.cloud == "onprem"] == [f"192.168.56.{i}" for i in range(11, 17)]


def test_host_resolution_failures():
    with pytest.raises(RuntimeError, match="apply lab/aws first"):
        resolve_hosts(MACHINES, FakeShell(codes={"public_ips": 1}))
    with pytest.raises(RuntimeError, match="acquired-02: lab/aws has no machine called acquired-02"):
        resolve_hosts(MACHINES, FakeShell(aws={"acquired-01": "198.51.100.10"}))
    shell = FakeShell()
    onprem = [m for m in MACHINES if m.cloud == "onprem"]
    assert resolve_hosts(onprem, shell) == onprem and shell.calls == []


def test_settings_and_secret():
    assert connect_settings(FakeShell()) == SETTINGS
    assert fetch_secret(FakeShell(), SETTINGS) == SECRET
    with pytest.raises(RuntimeError, match="infra apply first"):
        connect_settings(FakeShell(codes={"output -json connect": 1}))
    with pytest.raises(RuntimeError, match="onboarding is closed"):
        connect_settings(FakeShell(settings={**SETTINGS, "secret_name": None}))
    with pytest.raises(RuntimeError, match="operator_ip_ranges"):
        fetch_secret(FakeShell(codes={"keyvault": 1}), SETTINGS)


# --- The scripts ----------------------------------------------------------------------------------------


def test_a_linux_script():
    command, script = script_for(machine("branch-02-brandon"), SETTINGS, SECRET)
    assert command[-2:] == ["vagrant@192.168.56.12", "sudo bash -s"]
    assert onboard.LINUX_AGENT in script and "command -v azcmagent" in script
    assert '"--resource-name" "branch-02-brandon"' in script
    assert '"--tags" "site=brandon,ring=branch,os=linux,cloud=onprem"' in script
    assert '"--location" "canadacentral"' in script and '"--resource-group" "rg-oneview-prod"' in script


def test_a_windows_script():
    command, script = script_for(machine("branch-05-winkler"), SETTINGS, SECRET)
    assert command[-1] == "powershell -NoProfile -NonInteractive -Command -"
    assert "\r\n" in script and onboard.WINDOWS_AGENT in script and "msiexec.exe" in script
    assert "exit $LASTEXITCODE" in script and "ring=test,os=windows" in script


@pytest.mark.parametrize("name", ["branch-01-winnipeg", "branch-06-selkirk", "acquired-01"])
def test_the_secret_never_appears_in_a_command_line(name):
    command, script = script_for(machine(name), SETTINGS, SECRET)
    assert all(SECRET not in part for part in command)
    assert SECRET not in script  # only base64, decoded on the machine itself
    assert base64.b64encode(SECRET.encode()).decode() in script


# --- Onboarding ----------------------------------------------------------------------------------------


def clock():
    t = {"now": 0.0}

    def now():
        t["now"] += 30
        return t["now"]

    return now


def test_onboarding_connects_only_what_isnt_connected():
    machines = resolve_hosts(MACHINES, FakeShell())
    shell = FakeShell(connected={"branch-01-winnipeg", "acquired-02"})
    outcomes = onboard.onboard(machines, SETTINGS, SECRET, shell, now=clock())
    assert [o.status for o in outcomes].count("already connected") == 2
    assert [o.status for o in outcomes].count("connected") == 6
    assert all(o.seconds == 30 for o in outcomes if o.status == "connected")
    sent = [stdin for args, stdin in shell.calls if stdin and "connect" in stdin]
    assert len(sent) == 6


def test_failures_are_reported_without_the_secret():
    machines = resolve_hosts(MACHINES, FakeShell())
    outcomes = onboard.onboard(machines, SETTINGS, SECRET, FakeShell(failing={"branch-03-steinbach"}), now=clock())
    failed = [o for o in outcomes if o.status == "failed"]
    assert [o.name for o in failed] == ["branch-03-steinbach"]
    assert "Invalid client secret" in failed[0].detail and SECRET not in failed[0].detail
    assert "Connected 7 of 8 machines." in onboard.to_markdown(outcomes)


def test_is_connected_on_both_systems():
    shell = FakeShell(connected={"branch-05-winkler"})
    assert is_connected(machine("branch-05-winkler"), shell)
    assert not is_connected(machine("branch-02-brandon"), shell)
    assert not is_connected(machine("branch-02-brandon"), FakeShell(codes={"azcmagent show": 255}))


# --- The command line ------------------------------------------------------------------------------------


def test_a_dry_run_shows_scripts_but_never_the_secret(capsys):
    assert onboard.main(["--dry-run", "--only", "branch-01-winnipeg", "branch-05-winkler"], shell=FakeShell()) == 0
    out = capsys.readouterr().out
    assert "# branch-01-winnipeg: ssh" in out and "# branch-05-winkler: ssh" in out and "acquired" not in out
    assert SECRET not in out and base64.b64encode(SECRET.encode()).decode() not in out and "***" in out


def test_a_run_saves_results(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(onboard, "RESULTS", tmp_path)
    assert onboard.main([], shell=FakeShell()) == 0
    saved = json.loads(next(tmp_path.glob("onboarding-*.json")).read_text())
    assert len(saved) == 8 and "Connected 8 of 8 machines." in capsys.readouterr().out
    assert onboard.main(["--only", "branch-04-thompson"], shell=FakeShell(failing={"branch-04-thompson"})) == 1


def test_unknown_machines_are_refused():
    with pytest.raises(SystemExit):
        onboard.main(["--only", "branch-99-nowhere"], shell=FakeShell())
