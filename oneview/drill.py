"""The emergency patch drill: a critical patch is out, so how long until every server has it?

    python -m oneview.drill                    # show what it would do, and change nothing
    python -m oneview.drill --yes              # run it: the test ring first, then the branches
    python -m oneview.drill --yes --rings test

For each ring in turn, every server in it is told to install critical and security updates now,
rebooting if it needs to. Then every few minutes each server still going is asked to check again,
until a check made after its ring started finds nothing missing. The branches start only once every
test server is done, as in a real rollout; if the test ring doesn't finish, the drill stops there.

Hours to patch run from the drill's start to that clean check, so they cover the whole job: the
install, any reboot, and proof that it worked.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from oneview import patching
from oneview.graph import Shell, az_json, kql, query, run_shell

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
API = patching.API
RINGS = ("test", "branch")
UPDATES = {"classificationsToInclude": ["Critical", "Security"]}


def _now() -> datetime:  # pragma: no cover - real clock
    return datetime.now(UTC)


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 2)


class Drill:
    def __init__(self, shell: Shell, resource_group: str, now: Callable[[], datetime] = _now, sleep=time.sleep):
        self.shell, self.resource_group, self.now, self.sleep = shell, resource_group, now, sleep
        self._base: str | None = None

    def url(self, name: str, action: str) -> str:
        if self._base is None:
            subscription = az_json(self.shell, ["account", "show", "--query", "id", "-o", "tsv"])
            self._base = (
                f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{self.resource_group}"
                "/providers/Microsoft.HybridCompute/machines"
            )
        return f"{self._base}/{name}/{action}?api-version={API}"

    def install(self, name: str, hours: int) -> None:
        body = {
            "maximumDuration": f"PT{hours}H",
            "rebootSetting": "IfRequired",
            "linuxParameters": UPDATES,
            "windowsParameters": UPDATES,
        }
        az_json(self.shell, ["rest", "--method", "post", "--url", self.url(name, "installPatches"), "--body", json.dumps(body)])

    def assess(self, name: str) -> None:
        az_json(self.shell, ["rest", "--method", "post", "--url", self.url(name, "assessPatches")])

    def machines(self) -> list[dict]:
        return query(self.shell, kql("machines", self.resource_group))

    def fleet(self, machines: list[dict]) -> dict[str, patching.Server]:
        assessments = query(self.shell, kql("patch-assessment", self.resource_group))
        return {s.name: s for s in patching.servers(machines, assessments)}

    def checked_at(self, server: patching.Server) -> datetime:
        try:
            return datetime.fromisoformat(server.assessed_at or "")
        except ValueError:
            return self.now()

    def run(self, rings: list[str], poll_minutes: float = 5, timeout_hours: float = 4) -> dict:
        start = self.now()
        machines = self.machines()
        before = self.fleet(machines)
        results: dict[str, dict] = {}
        held: list[str] = []
        ring_hours: dict[str, float] = {}

        for i, ring in enumerate(rings):
            names = sorted(m["name"] for m in machines if m.get("ring") == ring)
            if not names:
                continue
            ring_start = self.now()
            for name in names:
                result = {"name": name, "ring": ring, "missing_at_start": before[name].missing, "hours": None}
                try:
                    self.install(name, hours=max(1, round(timeout_hours)))
                    result["status"] = "installing"
                except RuntimeError as e:
                    result["status"] = f"install failed: {e}"
                results[name] = result
            pending = [n for n in names if results[n]["status"] == "installing"]
            deadline = ring_start + timedelta(hours=timeout_hours)

            while pending and self.now() < deadline:
                self.sleep(poll_minutes * 60)
                for name in pending:
                    with contextlib.suppress(RuntimeError):  # busy installing or rebooting; ask again next round
                        self.assess(name)
                latest = self.fleet(machines)
                for name in list(pending):
                    server = latest[name]
                    if server.assessed and server.missing == 0 and self.checked_at(server) >= ring_start:
                        results[name] |= {"status": "patched", "hours": _hours(start, self.checked_at(server))}
                        pending.remove(name)

            for name in pending:
                results[name]["status"] = f"not done after {timeout_hours:g} hours"
            ring_hours[ring] = _hours(ring_start, self.now())
            if any(results[n]["status"] != "patched" for n in names):
                held = list(rings[i + 1 :])
                break

        return {
            "started_at": start.isoformat(timespec="seconds"),
            "finished_at": self.now().isoformat(timespec="seconds"),
            "rings": ring_hours,
            "held": held,
            "servers": sorted(results.values(), key=lambda r: (rings.index(r["ring"]), r["name"])),
        }


def to_markdown(result: dict) -> str:
    servers = result["servers"]
    patched = [s for s in servers if s["status"] == "patched"]
    last = max((s["hours"] for s in patched), default=None)
    head = f"**Emergency patch drill: {len(patched)} of {len(servers)} servers patched"
    head += f", the last {last:g} hours after the start.**" if last is not None else ".**"
    rings = ", ".join(f"{ring} ring took {hours:g} h" for ring, hours in result["rings"].items())
    lines = [f"{head} {rings[0].upper() + rings[1:]}." if rings else head]
    if result["held"]:
        lines.append(f"Held back: the {' and '.join(result['held'])} ring, because the ring before it didn't finish.")
    lines += ["", "| Server | Ring | Missing at start | Hours to patch | Result |", "|---|---|---|---|---|"]
    for s in servers:
        hours = "-" if s["hours"] is None else f"{s['hours']:g}"
        lines.append(f"| {s['name']} | {s['ring']} | {s['missing_at_start']} | {hours} | {s['status']} |")
    return "\n".join(lines) + "\n"


def plan(machines: list[dict], rings: list[str]) -> str:
    lines = ["Would tell these servers to install critical and security updates now, rebooting where needed:"]
    for ring in rings:
        names = sorted(m["name"] for m in machines if m.get("ring") == ring)
        lines.append(f"- {ring} ring ({len(names)}): {', '.join(names) or 'none'}")
    lines.append("Each ring starts only when the one before it is done. Run again with --yes to start.")
    return "\n".join(lines) + "\n"


def save(result: dict, folder: Path | None = None) -> Path:
    folder = folder or RESULTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"drill-{result['started_at'][:16].replace(':', '')}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None, shell: Shell = run_shell, now: Callable[[], datetime] = _now, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description="Time how long it takes to patch every OneView server.")
    parser.add_argument("--resource-group", default="rg-oneview-prod")
    parser.add_argument("--rings", nargs="+", choices=RINGS, default=list(RINGS))
    parser.add_argument("--poll-minutes", type=float, default=5)
    parser.add_argument("--timeout-hours", type=float, default=4, help="per ring")
    parser.add_argument("--yes", action="store_true", help="really start: servers install updates and may reboot")
    args = parser.parse_args(argv)

    drill = Drill(shell, args.resource_group, now=now, sleep=sleep)
    if not args.yes:
        print(plan(drill.machines(), args.rings))
        return 0
    result = drill.run(args.rings, poll_minutes=args.poll_minutes, timeout_hours=args.timeout_hours)
    print(to_markdown(result))
    print(f"Saved {save(result)}")
    return 0 if not result["held"] and all(s["status"] == "patched" for s in result["servers"]) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
