"""Aggregation over usage records: run / stage / experiment / benchmark.

Collects every ``usage.jsonl`` under the given run directories and
groups by any record fields (default: stage, model). Answers the plan's
target questions: tokens by stage, cost by experiment.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from proctor.usage.tracker import read_usage

_SUM_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "latency_s",
    "cost_usd",
)


def collect(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.is_file() and path.name.endswith(".jsonl"):
            records.extend(read_usage(path))
        elif path.is_dir():
            for file in sorted(path.rglob("usage.jsonl")):
                records.extend(read_usage(file))
    return records


def aggregate(
    records: list[dict[str, Any]], group_by: list[str]
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        key = tuple(record.get(field) for field in group_by)
        row = groups.get(key)
        if row is None:
            row = {field: record.get(field) for field in group_by}
            row["calls"] = 0
            for field in _SUM_FIELDS:
                row[field] = 0.0
            row["cost_known"] = True
            groups[key] = row
        row["calls"] += 1
        for field in _SUM_FIELDS:
            value = record.get(field)
            if field == "cost_usd" and value is None and record.get("usage") != 0:
                if record.get("input_tokens") or record.get("output_tokens"):
                    row["cost_known"] = False
            if isinstance(value, (int, float)):
                row[field] += value
    result = []
    for row in groups.values():
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            row[field] = int(row[field])
        row["reasoning_tokens"] = int(row["reasoning_tokens"])
        row["latency_s"] = round(row["latency_s"], 1)
        row["cost_usd"] = None if not row["cost_known"] else round(row["cost_usd"], 4)
        del row["cost_known"]
        result.append(row)
    result.sort(key=lambda r: (-(r["cost_usd"] or 0), -r["input_tokens"]))
    return result


def render_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no usage records found)"
    headers = list(rows[0].keys())
    widths = {h: max(len(h), *(len(_cell(row[h])) for row in rows)) for h in headers}
    lines = ["  ".join(h.ljust(widths[h]) for h in headers)]
    lines.append("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        lines.append("  ".join(_cell(row[h]).ljust(widths[h]) for h in headers))
    return "\n".join(lines)


def render_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)
