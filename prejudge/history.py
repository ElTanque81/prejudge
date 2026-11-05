"""Per-project score history so every run shows progress since the last one."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _path(project: Path) -> Path:
    return project / ".prejudge" / "history.json"


def load(project: Path) -> list[dict]:
    p = _path(project)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def last_for(project: Path, rules_title: str) -> dict | None:
    runs = [r for r in load(project) if r.get("rules") == rules_title]
    return runs[-1] if runs else None


def append(project: Path, rules_title: str, total: float, scores: dict[str, int], session_id: str) -> None:
    runs = load(project)
    runs.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rules": rules_title,
            "total": total,
            "scores": scores,
            "session_id": session_id,
        }
    )
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(runs[-50:], indent=1), encoding="utf-8")
