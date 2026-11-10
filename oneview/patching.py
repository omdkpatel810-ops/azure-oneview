"""Patch compliance: which servers are missing critical or security updates, by ring.

    python -m oneview.patching snapshot --assess --save before   # assess every server now, then report
    python -m oneview.patching snapshot --save after
    python -m oneview.patching compare results/patches-before.json results/patches-after.json

A server is compliant when its latest assessment found no missing critical or security updates.
A server that has never been assessed is not compliant: an unknown state is not a good one.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from oneview.graph import Shell, az_json, kql, query, run_shell

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
API = "2024-07-10"


@dataclass
class Server:
    name: str
    os: str
    ring: str
    site: str
    critical: int | None  # None: never assessed
    security: int | None
    other: int | None
    assessed_at: str | None

    @property
    def assessed(self) -> bool:
        return self.critical is not None

    @property
    def compliant(self) -> bool:
        return self.assessed and (self.critical or 0) + (self.security or 0) == 0

    @property
    def missing(self) -> int:
        return (self.critical or 0) + (self.security or 0)


def _count(counts: dict, *keys: str) -> int:
    lowered = {k.lower(): v for k, v in (counts or {}).items()}
    return sum(int(lowered.get(k.lower()) or 0) for k in keys)


def servers(machines: list[dict], assessments: list[dict]) -> list[Server]:
    latest = {a["machine"].lower(): a for a in assessments}
    result = []
    for m in sorted(machines, key=lambda m: m["name"]):
        a = latest.get(m["name"].lower())
        counts = (a or {}).get("counts") or {}
        result.append(
            Server(
                name=m["name"],
                os=m.get("os", ""),
                ring=m.get("ring", ""),
                site=m.get("site", ""),
                critical=_count(counts, "critical") if a else None,
                security=_count(counts, "security") if a else None,
                other=_count(counts, "other", "updates", "updateRollup", "featurePack", "servicePack", "definition", "tools")
                if a
                else None,
                assessed_at=a.get("assessedAt") if a else None,
            )
        )
    return result


def snapshot(shell: Shell, resource_group: str) -> dict:
    machines = query(shell, kql("machines", resource_group))
    assessments = query(shell, kql("patch-assessment", resource_group))
    return {
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "resource_group": resource_group,
        "servers": [asdict(s) for s in servers(machines, assessments)],
    }


def load(snap: dict) -> list[Server]:
    return [Server(**s) for s in snap["servers"]]


def summary(snap: dict) -> dict:
    fleet = load(snap)
    rings = sorted({s.ring for s in fleet})
    return {
        "servers": len(fleet),
        "compliant": sum(s.compliant for s in fleet),
        "compliance": round(100 * sum(s.compliant for s in fleet) / len(fleet), 1) if fleet else 0.0,
        "missing_critical": sum(s.critical or 0 for s in fleet),
        "missing_security": sum(s.security or 0 for s in fleet),
        "never_assessed": [s.name for s in fleet if not s.assessed],
        "by_ring": {r: f"{sum(s.compliant for s in fleet if s.ring == r)} of {sum(s.ring == r for s in fleet)}" for r in rings},
    }


def assess(shell: Shell, resource_group: str, names: list[str]) -> None:
    """Ask every server to check for missing updates now, instead of waiting for the daily check."""
    subscription = az_json(shell, ["account", "show", "--query", "id", "-o", "tsv"])
    for name in names:
        url = (
            f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.HybridCompute/machines/{name}/assessPatches?api-version={API}"
        )
        az_json(shell, ["rest", "--method", "post", "--url", url])


def to_markdown(snap: dict) -> str:
    s = summary(snap)
    lines = [
        f"**Patch compliance: {s['compliant']} of {s['servers']} servers ({s['compliance']:g}%)**, "
        f"{s['missing_critical']} critical and {s['missing_security']} security updates missing. "
        + ", ".join(f"{ring} ring: {v} compliant" for ring, v in s["by_ring"].items())
        + ".",
        "",
        "| Server | Ring | OS | Critical | Security | Other | Assessed |",
        "|---|---|---|---|---|---|---|",
    ]
    for srv in load(snap):
        if srv.assessed:
            counts = f"{srv.critical} | {srv.security} | {srv.other}"
            lines.append(f"| {srv.name} | {srv.ring} | {srv.os} | {counts} | {srv.assessed_at} |")
        else:
            lines.append(f"| {srv.name} | {srv.ring} | {srv.os} | - | - | - | never |")
    return "\n".join(lines) + "\n"


def compare(before: dict, after: dict) -> str:
    b, a = summary(before), summary(after)
    old = {s.name: s for s in load(before)}
    lines = [
        f"**Patch compliance: {b['compliance']:g}% before, {a['compliance']:g}% after.** "
        f"Missing critical and security updates: {b['missing_critical'] + b['missing_security']} before, "
        f"{a['missing_critical'] + a['missing_security']} after.",
        "",
        "| Server | Missing before | Missing after |",
        "|---|---|---|",
    ]
    for srv in load(after):
        prev = old.get(srv.name)
        was = "-" if prev is None or not prev.assessed else str(prev.missing)
        now = str(srv.missing) if srv.assessed else "-"
        lines.append(f"| {srv.name} | {was} | {now} |")
    return "\n".join(lines) + "\n"


def save(snap: dict, label: str, folder: Path | None = None) -> Path:
    folder = folder or RESULTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"patches-{label}.json"
    path.write_text(json.dumps(snap, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None, shell: Shell = run_shell) -> int:
    parser = argparse.ArgumentParser(description="Patch compliance for OneView's Arc servers.")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="report each server's missing updates now")
    snap.add_argument("--resource-group", default="rg-oneview-prod")
    snap.add_argument("--assess", action="store_true", help="ask every server to check for updates first")
    snap.add_argument("--save", metavar="LABEL", help="save as results/patches-LABEL.json")
    cmp_ = sub.add_parser("compare", help="compare two saved snapshots")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    args = parser.parse_args(argv)

    if args.command == "compare":
        print(compare(json.loads(args.before.read_text()), json.loads(args.after.read_text())))
        return 0
    if args.assess:
        names = [m["name"] for m in query(shell, kql("machines", args.resource_group))]
        assess(shell, args.resource_group, names)
        print(f"Asked {len(names)} servers to check for updates; results take a few minutes. Run again without --assess.")
        return 0
    result = snapshot(shell, args.resource_group)
    print(to_markdown(result))
    if args.save:
        print(f"Saved {save(result, args.save)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
