"""Run Azure Resource Graph queries through the Azure CLI."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

QUERIES = Path(__file__).resolve().parent / "queries"
RESOURCE_GROUP = re.compile(r"^[-\w.()]{1,90}$")

# run(args, stdin) -> (exit code, output)
Shell = Callable[[list[str], str | None], tuple[int, str]]


def run_shell(args: list[str], stdin: str | None = None) -> tuple[int, str]:  # pragma: no cover - real commands
    p = subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout + p.stderr


def kql(name: str, resource_group: str) -> str:
    """A query from queries/, for one resource group."""
    if not RESOURCE_GROUP.match(resource_group):
        raise ValueError(f"not a resource group name: {resource_group!r}")
    return (QUERIES / f"{name}.kql").read_text().replace("{resource_group}", resource_group)


def query(shell: Shell, text: str) -> list[dict]:
    code, out = shell(["az", "graph", "query", "-q", text, "--first", "1000", "-o", "json"], None)
    if code != 0:
        raise RuntimeError(f"Resource Graph query failed: {out.strip()[-300:]}")
    value = json.loads(out)
    rows = value.get("data", []) if isinstance(value, dict) else value
    return [r for r in rows if isinstance(r, dict)]


def az_json(shell: Shell, args: list[str]) -> str:
    code, out = shell(["az", *args], None)
    if code != 0:
        raise RuntimeError(f"az {' '.join(args[:3])} failed: {out.strip()[-300:]}")
    return out.strip()
