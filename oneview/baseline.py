"""Security baseline scores: how many of the Azure compute security baseline's rules each server passes.

    python -m oneview.baseline snapshot --save before
    # ...fix things: vagrant provision --provision-with harden
    python -m oneview.baseline snapshot --save after      # after the next check, about 15 minutes
    python -m oneview.baseline compare results/baseline-before.json results/baseline-after.json

A server's score is the share of rules it passes. Defender for Cloud's failing recommendations for
each server are counted alongside, when there are any.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from oneview.graph import Shell, kql, query, run_shell

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
SEVERITIES = ("High", "Medium", "Low")


@dataclass
class Server:
    name: str
    baseline: str | None  # None: no report yet
    passed: int
    failed: list[str]  # failing rules, each with its first reason
    checked_at: str | None
    findings: dict[str, int] = field(default_factory=dict)  # Defender recommendations failed, by severity

    @property
    def total(self) -> int:
        return self.passed + len(self.failed)

    @property
    def score(self) -> float | None:
        return round(100 * self.passed / self.total, 1) if self.total else None


def _passes(rule: dict) -> bool:
    return str(rule.get("complianceStatus")).lower() in ("true", "compliant")


def _describe(rule: dict) -> str:
    name = str(rule.get("resourceId") or rule.get("name") or "unnamed rule")
    reasons = [r.get("phrase", "").strip() for r in rule.get("reasons") or [] if isinstance(r, dict)]
    reason = next((r for r in reasons if r), "")
    return f"{name}: {reason}" if reason else name


def servers(machines: list[dict], reports: list[dict], findings: list[dict]) -> list[Server]:
    by_machine = {r["machine"].lower(): r for r in reports}
    defender: dict[str, dict[str, int]] = {}
    for f in findings:
        counts = defender.setdefault(f["machine"].lower(), {})
        severity = f.get("severity") or "Unknown"
        counts[severity] = counts.get(severity, 0) + 1
    result = []
    for m in sorted(machines, key=lambda m: m["name"]):
        report = by_machine.get(m["name"].lower())
        rules = [r for r in (report or {}).get("rules") or [] if isinstance(r, dict)]
        result.append(
            Server(
                name=m["name"],
                baseline=report["baseline"] if report else None,
                passed=sum(_passes(r) for r in rules),
                failed=sorted(_describe(r) for r in rules if not _passes(r)),
                checked_at=report.get("checkedAt") if report else None,
                findings=defender.get(m["name"].lower(), {}),
            )
        )
    return result


def snapshot(shell: Shell, resource_group: str) -> dict:
    machines = query(shell, kql("machines", resource_group))
    reports = query(shell, kql("baseline", resource_group))
    findings = query(shell, kql("defender", resource_group))
    return {
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "resource_group": resource_group,
        "servers": [asdict(s) for s in servers(machines, reports, findings)],
    }


def load(snap: dict) -> list[Server]:
    return [Server(**s) for s in snap["servers"]]


def summary(snap: dict) -> dict:
    fleet = load(snap)
    scored = [s for s in fleet if s.score is not None]
    return {
        "servers": len(fleet),
        "reporting": len(scored),
        "average_score": round(sum(s.score for s in scored) / len(scored), 1) if scored else None,
        "rules_failed": sum(len(s.failed) for s in fleet),
        "fully_compliant": sum(s.score == 100 for s in scored),
        "no_report": [s.name for s in fleet if s.score is None],
        "defender_findings": sum(sum(s.findings.values()) for s in fleet),
    }


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:g}%"


def _findings(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    order = [*SEVERITIES, *sorted(set(counts) - set(SEVERITIES))]
    return ", ".join(f"{counts[k]} {k.lower()}" for k in order if k in counts)


def to_markdown(snap: dict, worst: int = 5) -> str:
    s = summary(snap)
    lines = [
        f"**Security baseline: {_score(s['average_score'])} of rules passed on average, across {s['reporting']} of "
        f"{s['servers']} servers reporting.** {s['rules_failed']} failing rules; "
        f"{s['fully_compliant']} servers pass every rule; {s['defender_findings']} Defender for Cloud findings.",
        "",
        "| Server | Baseline | Score | Rules failed | Defender findings |",
        "|---|---|---|---|---|",
    ]
    fleet = load(snap)
    for srv in fleet:
        baseline = srv.baseline or "no report yet"
        rules = f"{len(srv.failed)} of {srv.total}"
        lines.append(f"| {srv.name} | {baseline} | {_score(srv.score)} | {rules} | {_findings(srv.findings)} |")
    for srv in fleet:
        if srv.failed:
            lines += ["", f"**{srv.name}** fails:"]
            lines += [f"- {rule}" for rule in srv.failed[:worst]]
            if len(srv.failed) > worst:
                lines.append(f"- and {len(srv.failed) - worst} more")
    return "\n".join(lines) + "\n"


def compare(before: dict, after: dict) -> str:
    b, a = summary(before), summary(after)
    old = {s.name: s for s in load(before)}
    lines = [
        f"**Security baseline: {_score(b['average_score'])} before, {_score(a['average_score'])} after.** "
        f"Failing rules: {b['rules_failed']} before, {a['rules_failed']} after.",
        "",
        "| Server | Before | After | Rules fixed | Newly failing |",
        "|---|---|---|---|---|",
    ]
    for srv in load(after):
        prev = old.get(srv.name)
        was = set(prev.failed) if prev else set()
        now = set(srv.failed)
        lines.append(
            f"| {srv.name} | {_score(prev.score if prev else None)} | {_score(srv.score)} | {len(was - now)} | {len(now - was)} |"
        )
    return "\n".join(lines) + "\n"


def save(snap: dict, label: str, folder: Path | None = None) -> Path:
    folder = folder or RESULTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"baseline-{label}.json"
    path.write_text(json.dumps(snap, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None, shell: Shell = run_shell) -> int:
    parser = argparse.ArgumentParser(description="Security baseline scores for OneView's Arc servers.")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="each server's baseline score now")
    snap.add_argument("--resource-group", default="rg-oneview-prod")
    snap.add_argument("--save", metavar="LABEL", help="save as results/baseline-LABEL.json")
    cmp_ = sub.add_parser("compare", help="compare two saved snapshots")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    args = parser.parse_args(argv)

    if args.command == "compare":
        print(compare(json.loads(args.before.read_text()), json.loads(args.after.read_text())))
        return 0
    result = snapshot(shell, args.resource_group)
    print(to_markdown(result))
    if args.save:
        print(f"Saved {save(result, args.save)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
