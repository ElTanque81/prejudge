"""Deterministic parsing of a hackathon rules file (markdown or pasted page text).

The rubric weights are parsed here, not by the model, so the final weighted
score is always computed from the organizer's own numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WEIGHT = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(?P<name>[^:\n]{3,120}?)\s*[:\-–—]\s*(?P<pct>\d{1,3})\s*%\s*$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[[ xX]?\])\s+(?P<text>.+?)\s*$")
_HEADING = re.compile(r"^\s*(?:#+\s*)?(?P<h>[A-Z][^.!?\n]{2,60})\s*$")
_CHECKLIST_HINT = re.compile(r"checklist|submission requirement|your pr should include|must include|deliverable", re.I)


@dataclass
class Criterion:
    name: str
    weight: float  # 0..1


@dataclass
class Rules:
    title: str
    criteria: list[Criterion]
    checklist: list[str]
    raw: str = field(repr=False)

    @property
    def weights_ok(self) -> bool:
        return abs(sum(c.weight for c in self.criteria) - 1.0) < 0.02


def parse_rules(text: str) -> Rules:
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.strip()), "Hackathon")

    criteria: list[Criterion] = []
    seen: set[str] = set()
    for ln in lines:
        m = _WEIGHT.match(ln)
        if not m:
            continue
        name = m.group("name").strip(" *_`")
        pct = int(m.group("pct"))
        if 0 < pct <= 100 and name.lower() not in seen:
            seen.add(name.lower())
            criteria.append(Criterion(name=name, weight=pct / 100))

    return Rules(title=title, criteria=criteria, checklist=_checklist(lines), raw=text)


def _checklist(lines: list[str]) -> list[str]:
    """Bullets after a checklist hint; for pasted page text (no bullets) the first block of short lines."""
    for i, ln in enumerate(lines):
        if not _CHECKLIST_HINT.search(ln) or _BULLET.match(ln):
            continue
        rest = lines[i + 1 :]
        bullets: list[str] = []
        for r in rest:
            b = _BULLET.match(r)
            if b:
                bullets.append(b.group("text"))
            elif r.strip() and bullets:
                break
            elif r.strip() and _CHECKLIST_HINT.search(r):
                continue  # e.g. "Your PR should include:" under a "Checklist" heading
            elif r.strip():
                break  # prose before any bullet: fall back to plain-line mode
        if bullets:
            return bullets
        plain: list[str] = []
        for r in rest:
            s = r.strip()
            if not s:
                if plain:
                    break
                continue
            if _CHECKLIST_HINT.search(s) and s.endswith(":"):
                continue
            if len(s) > 160:
                break
            plain.append(s)
        if len(plain) >= 2:
            return plain
    return []


def weighted_total(rules: Rules, scores: dict[str, int]) -> float:
    """Return 0..100. Criteria without a score count as 0."""
    if not rules.criteria:
        return 0.0
    total_w = sum(c.weight for c in rules.criteria) or 1.0
    acc = sum(c.weight * scores.get(c.name, 0) for c in rules.criteria)
    return round(acc / total_w * 10, 1)
