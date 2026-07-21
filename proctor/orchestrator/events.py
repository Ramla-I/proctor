"""``events.jsonl`` — one line per orchestration event.

Synchronous appends (microseconds against stage runtimes measured in
minutes) so a killed run loses nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            data = json.loads(line)
            if isinstance(data, dict):
                events.append(data)
    return events
