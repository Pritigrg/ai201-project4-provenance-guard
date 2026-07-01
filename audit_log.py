from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def append_audit_log(path: Path, entry: dict[str, Any]) -> None:
    """Append one structured audit-log entry as a single JSON line (JSONL)."""
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_audit_log(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent audit-log entries, newest first.

    Reads the JSONL file one object per line. Malformed lines are skipped so a
    single bad write never breaks GET /log. A non-positive limit returns all
    entries (still newest first).
    """
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries.reverse()  # newest first
    if limit <= 0:
        return entries
    return entries[:limit]
