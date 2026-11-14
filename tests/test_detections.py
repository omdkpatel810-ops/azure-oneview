"""The Sentinel alert rules in detections/: complete, consistent, and their patterns read real sshd lines.

KQL can't run offline, so the queries themselves are checked in Azure on the first real run. What can be
checked here: every rule has a query, the columns its incidents name exist, and the regular expressions
the queries use pull the right fields out of real OpenSSH log messages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DETECTIONS = Path(__file__).resolve().parent.parent / "detections"
RULES = json.loads((DETECTIONS / "rules.json").read_text())
TACTICS = {
    "Reconnaissance", "ResourceDevelopment", "InitialAccess", "Execution", "Persistence", "PrivilegeEscalation",
    "DefenseEvasion", "CredentialAccess", "Discovery", "LateralMovement", "Collection", "Exfiltration",
    "CommandAndControl", "Impact",
}  # fmt: skip


def query(name: str) -> str:
    return (DETECTIONS / f"{name}.kql").read_text()


def pattern(name: str, column: str) -> re.Pattern:
    """The regular expression a query uses for one column, as Python sees it."""
    found = re.search(rf'{column} = extract\(@"(.+?)", 1, SyslogMessage\)', query(name))
    assert found, f"{name} has no extract() for {column}"
    return re.compile(found.group(1))


def test_every_rule_has_a_query_and_every_query_a_rule():
    assert set(RULES) == {p.stem for p in DETECTIONS.glob("*.kql")}


@pytest.mark.parametrize("name", sorted(RULES))
def test_each_rule_is_complete(name):
    rule = RULES[name]
    assert rule["severity"] in ("Informational", "Low", "Medium", "High")
    assert set(rule["tactics"]) <= TACTICS
    assert all(re.fullmatch(r"T\d{4}", t) for t in rule["techniques"])
    assert set(rule["entities"]) <= {"Host", "IP", "Account"} and "Host" in rule["entities"]
    text = query(name)
    assert text.startswith("// ") and text.splitlines()[0][3:] in rule["description"]
    for column in rule["entities"].values():  # each column an incident names is in the query's output
        assert re.search(rf"\b{column}\b", text.split("summarize")[-1].split("project")[-1]), f"{name}: no {column}"


@pytest.mark.parametrize("name", ["ssh-brute-force", "windows-failed-logons"])
def test_bursts_are_ten_or_more(name):
    assert RULES[name]["description"].startswith("Ten or more") and "| where Attempts >= 10" in query(name)


def test_the_admin_rule_watches_only_the_administrators_group():
    assert 'EventID == 4732 and TargetSid == "S-1-5-32-544"' in query("windows-new-local-admin")


FAILED = [
    ("Failed password for alice from 203.0.113.50 port 51234 ssh2", "alice", "203.0.113.50"),
    ("Failed password for invalid user admin from 203.0.113.50 port 51236 ssh2", "admin", "203.0.113.50"),
    ("Failed password for root from 2001:db8::25 port 40022 ssh2", "root", "2001:db8::25"),
]
NOT_FAILED = [
    "Invalid user admin from 203.0.113.50 port 51236",  # the same attempt as the line above it; count it once
    "Accepted password for alice from 203.0.113.50 port 51234 ssh2",
    "Connection closed by authenticating user root 203.0.113.50 port 51240 [preauth]",
    "pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=203.0.113.50",
]


@pytest.mark.parametrize(("line", "account", "address"), FAILED)
def test_failed_password_lines(line, account, address):
    assert pattern("ssh-brute-force", "Account").search(line).group(1) == account
    assert pattern("ssh-brute-force", "SourceIP").search(line).group(1) == address


@pytest.mark.parametrize("line", NOT_FAILED)
def test_lines_that_arent_failed_passwords(line):
    assert pattern("ssh-brute-force", "Account").search(line) is None


@pytest.mark.parametrize(
    ("line", "method", "address"),
    [
        ("Accepted password for root from 192.168.56.1 port 50122 ssh2", "password", "192.168.56.1"),
        ("Accepted publickey for root from 192.168.56.1 port 50122 ssh2: ED25519 SHA256:x", "publickey", "192.168.56.1"),
    ],
)
def test_root_logins(line, method, address):
    assert pattern("ssh-root-login", "Method").search(line).group(1) == method
    assert pattern("ssh-root-login", "SourceIP").search(line).group(1) == address


@pytest.mark.parametrize(
    "line",
    [
        "Accepted publickey for vagrant from 192.168.56.1 port 50122 ssh2",
        "Accepted password for rooted from 192.168.56.1 port 50122 ssh2",
        "Failed password for root from 192.168.56.1 port 50122 ssh2",
    ],
)
def test_logins_that_arent_root(line):
    assert pattern("ssh-root-login", "Method").search(line) is None
