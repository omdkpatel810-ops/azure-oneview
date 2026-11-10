"""A stand-in for `az`: Resource Graph answers from fixture rows, and every other call recorded."""

from __future__ import annotations

import json


class FakeAz:
    def __init__(self, tables: dict[str, list[dict]] | None = None, fail: str | None = None):
        self.tables = tables or {}
        self.fail = fail  # a word that makes any command containing it fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        self.calls.append(args)
        text = " ".join(args)
        if self.fail and self.fail in text:
            return 1, f"ERROR: {self.fail} went wrong"
        if args[:3] == ["az", "graph", "query"]:
            kql = args[4]
            for marker, rows in self.tables.items():
                if marker in kql:
                    return 0, json.dumps({"count": len(rows), "data": rows})
            return 0, json.dumps({"count": 0, "data": []})
        if args[:3] == ["az", "account", "show"]:
            return 0, "00000000-0000-0000-0000-000000000000\n"
        return 0, "{}"
