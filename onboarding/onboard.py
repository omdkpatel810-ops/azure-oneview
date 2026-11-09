"""Connect every machine in lab/inventory.json to Azure Arc, and time it.

Run from the operator's laptop (it reaches the branch VMs on the host-only network and the acquired
company's VMs over the internet), signed in with `az login`, after `terraform -chdir=infra apply`:

    python onboarding/onboard.py              # connect every machine that isn't connected yet
    python onboarding/onboard.py --dry-run    # show the scripts, with the secret replaced by ***
    python onboarding/onboard.py --only branch-05-winkler

For each machine it checks over SSH whether the Connected Machine agent is already connected, and
if not, sends a script that installs the agent and connects it, tagged with the machine's site and
patch ring. The onboarding secret is read from Key Vault into memory and reaches each machine only
through SSH's standard input: never in a command line, a file on this computer, or the output.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "lab" / "inventory.json"
RESULTS = ROOT / "onboarding" / "results"
LINUX_AGENT = "https://gbl.his.arc.azure.com/azcmagent-linux"
WINDOWS_AGENT = "https://aka.ms/AzureConnectedMachineAgent"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
NAME = re.compile(r"^[a-z][a-z0-9-]{2,40}$")

# run(args, stdin) -> (exit code, output)
Shell = Callable[[list[str], str | None], tuple[int, str]]


def run_shell(args: list[str], stdin: str | None = None) -> tuple[int, str]:  # pragma: no cover - real commands
    p = subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout + p.stderr


@dataclass(frozen=True)
class Machine:
    name: str
    os: str  # linux or windows
    site: str
    ring: str  # test (patched first) or branch
    cloud: str  # onprem or aws
    host: str
    user: str

    @property
    def tags(self) -> str:
        return f"site={self.site},ring={self.ring},os={self.os},cloud={self.cloud}"


def load_inventory(path: Path = INVENTORY) -> list[Machine]:
    data = json.loads(path.read_text())
    machines = [Machine(**m) for m in data["machines"]]
    problems = []
    names = [m.name for m in machines]
    if len(names) != len(set(names)):
        problems.append("machine names must be unique")
    for m in machines:
        if not NAME.match(m.name):
            problems.append(f"{m.name}: names are 3 to 41 lowercase letters, digits and hyphens")
        if m.os not in ("linux", "windows"):
            problems.append(f"{m.name}: os is linux or windows")
        if m.ring not in ("test", "branch"):
            problems.append(f"{m.name}: ring is test or branch")
        if m.cloud not in ("onprem", "aws"):
            problems.append(f"{m.name}: cloud is onprem or aws")
    if not any(m.ring == "test" for m in machines):
        problems.append("at least one machine must be in the test ring, so patches have somewhere to go first")
    if problems:
        raise ValueError("; ".join(problems))
    return machines


def resolve_hosts(machines: list[Machine], shell: Shell) -> list[Machine]:
    """Replace aws:<name> hosts with the public IPs lab/aws created."""
    if not any(m.host.startswith("aws:") for m in machines):
        return machines
    code, out = shell(["terraform", f"-chdir={ROOT / 'lab' / 'aws'}", "output", "-json", "public_ips"], None)
    if code != 0:
        raise RuntimeError("couldn't read lab/aws outputs; apply lab/aws first")
    ips = json.loads(out)
    resolved = []
    for m in machines:
        if m.host.startswith("aws:"):
            key = m.host.removeprefix("aws:")
            if key not in ips:
                raise RuntimeError(f"{m.name}: lab/aws has no machine called {key}")
            m = Machine(**{**asdict(m), "host": ips[key]})
        resolved.append(m)
    return resolved


def connect_settings(shell: Shell) -> dict:
    code, out = shell(["terraform", f"-chdir={ROOT / 'infra'}", "output", "-json", "connect"], None)
    if code != 0:
        raise RuntimeError("couldn't read infra outputs; run terraform -chdir=infra apply first")
    settings = json.loads(out)
    if not settings.get("secret_name"):
        raise RuntimeError("onboarding is closed (onboarding_open = false); open it to connect machines")
    return settings


def fetch_secret(shell: Shell, settings: dict) -> str:
    code, out = shell(
        [
            "az",
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            settings["key_vault"],
            "--name",
            settings["secret_name"],
            "--query",
            "value",
            "-o",
            "tsv",
        ],
        None,
    )
    if code != 0 or not out.strip():
        raise RuntimeError("couldn't read the onboarding secret; is your IP in operator_ip_ranges?")
    return out.strip()


def connect_args(m: Machine, s: dict) -> list[str]:
    return [
        "connect",
        "--service-principal-id",
        s["client_id"],
        "--tenant-id",
        s["tenant_id"],
        "--subscription-id",
        s["subscription_id"],
        "--resource-group",
        s["resource_group"],
        "--location",
        s["location"],
        "--resource-name",
        m.name,
        "--cloud",
        "AzureCloud",
        "--tags",
        m.tags,
    ]


def script_for(m: Machine, s: dict, secret: str) -> tuple[list[str], str]:
    """The SSH command and the script sent on its standard input."""
    encoded = base64.b64encode(secret.encode()).decode()
    args = " ".join(f'"{a}"' for a in connect_args(m, s))
    ssh = ["ssh", *SSH_OPTIONS, f"{m.user}@{m.host}"]
    if m.os == "linux":
        script = "\n".join(
            [
                "set -euo pipefail",
                f"SECRET=\"$(printf %s '{encoded}' | base64 -d)\"",
                "if ! command -v azcmagent >/dev/null; then",
                f"  curl -fsSL -o /tmp/install_linux_azcmagent.sh {LINUX_AGENT}",
                "  bash /tmp/install_linux_azcmagent.sh",
                "fi",
                f'azcmagent {args} --service-principal-secret "$SECRET"',
                "",
            ]
        )
        return [*ssh, "sudo bash -s"], script
    script = "\r\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$secret = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))",
            '$agent = "$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe"',
            "if (-not (Test-Path $agent)) {",
            f'  Invoke-WebRequest -UseBasicParsing -Uri {WINDOWS_AGENT} -OutFile "$env:TEMP\\azcmagent.msi"',
            '  Start-Process msiexec.exe -Wait -ArgumentList "/i `"$env:TEMP\\azcmagent.msi`" /qn"',
            "}",
            f"& $agent {args} --service-principal-secret $secret",
            "exit $LASTEXITCODE",
            "",
        ]
    )
    return [*ssh, "powershell -NoProfile -NonInteractive -Command -"], script


def is_connected(m: Machine, shell: Shell) -> bool:
    show = "azcmagent show" if m.os == "linux" else '& "$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe" show'
    remote = ["sudo " + show] if m.os == "linux" else ["powershell -NoProfile -Command -"]
    stdin = None if m.os == "linux" else show
    code, out = shell(["ssh", *SSH_OPTIONS, f"{m.user}@{m.host}", *remote], stdin)
    return code == 0 and bool(re.search(r"Agent Status\s*:\s*Connected", out))


@dataclass
class Outcome:
    name: str
    status: str  # connected, already connected, failed
    seconds: float
    detail: str = ""


def onboard(
    machines: list[Machine], settings: dict, secret: str, shell: Shell, now: Callable[[], float] = time.monotonic
) -> list[Outcome]:
    outcomes = []
    for m in machines:
        start = now()
        if is_connected(m, shell):
            outcomes.append(Outcome(m.name, "already connected", 0.0))
            continue
        command, script = script_for(m, settings, secret)
        code, out = shell(command, script)
        seconds = round(now() - start, 1)
        if code == 0:
            outcomes.append(Outcome(m.name, "connected", seconds))
        else:
            # Never echo the script; the agent's own message is enough, with the secret masked.
            outcomes.append(Outcome(m.name, "failed", seconds, out.replace(secret, "***")[-300:].strip()))
    return outcomes


def redacted(script: str, secret: str) -> str:
    return script.replace(base64.b64encode(secret.encode()).decode(), "***").replace(secret, "***")


def to_markdown(outcomes: list[Outcome]) -> str:
    done = sum(o.status != "failed" for o in outcomes)
    lines = [f"Connected {done} of {len(outcomes)} machines.", "", "| Machine | Result | Seconds |", "|---|---|---|"]
    lines += [f"| {o.name} | {o.status}{': ' + o.detail if o.detail else ''} | {o.seconds:g} |" for o in outcomes]
    return "\n".join(lines) + "\n"


def save(outcomes: list[Outcome], folder: Path | None = None) -> Path:
    folder = folder or RESULTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"onboarding-{datetime.now(UTC):%Y-%m-%dT%H%M}.json"
    path.write_text(json.dumps([asdict(o) for o in outcomes], indent=2) + "\n")
    return path


def main(argv: list[str] | None = None, shell: Shell = run_shell) -> int:
    parser = argparse.ArgumentParser(description="Connect the lab's machines to Azure Arc.")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--only", nargs="+", help="machine names to connect")
    parser.add_argument("--dry-run", action="store_true", help="print each script with the secret masked")
    args = parser.parse_args(argv)

    machines = load_inventory(args.inventory)
    if args.only:
        unknown = set(args.only) - {m.name for m in machines}
        if unknown:
            parser.error(f"not in the inventory: {', '.join(sorted(unknown))}")
        machines = [m for m in machines if m.name in args.only]
    machines = resolve_hosts(machines, shell)
    settings = connect_settings(shell)
    secret = fetch_secret(shell, settings)

    if args.dry_run:
        for m in machines:
            command, script = script_for(m, settings, secret)
            print(f"# {m.name}: {' '.join(command)}\n{redacted(script, secret)}")
        return 0
    outcomes = onboard(machines, settings, secret, shell)
    print(to_markdown(outcomes))
    print(f"Saved {save(outcomes)}. When every machine is connected, set onboarding_open = false and apply.")
    return 0 if all(o.status != "failed" for o in outcomes) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
