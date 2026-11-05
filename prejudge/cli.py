"""Command line entry point: ``prejudge RULES PROJECT``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from openminion import ProviderError, TurnTimeoutError

from . import __version__
from .repo_tools import Workspace
from .rules import parse_rules

LIBRARY = Path(__file__).parent / "rules_library"


def _load_rules(arg: str) -> str:
    p = Path(arg)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    lib = LIBRARY / f"{arg}.md"
    if lib.is_file():
        return lib.read_text(encoding="utf-8")
    known = ", ".join(sorted(x.stem for x in LIBRARY.glob("*.md")))
    raise SystemExit(f"rules not found: {arg} (file path or one of: {known})")


def _console_approval(auto_yes: bool):
    def approve(tool_name: str, args: dict, approval_id: str) -> bool:
        path = args.get("path", "?")
        preview = str(args.get("content", ""))[:400]
        print(f"\n--- approval {approval_id} ---\n{tool_name} -> {path}\n{preview}\n{'...' if len(str(args.get('content', ''))) > 400 else ''}")
        if auto_yes:
            print("auto-approved (--yes)")
            return True
        if not sys.stdin.isatty():
            print("denied (no interactive terminal; use --yes to allow)")
            return False
        return input("Create this file? [y/N] ").strip().lower() in {"y", "yes", "j", "ja"}

    return approve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prejudge", description="Judge your hackathon project with the organizer's rubric before you submit.")
    ap.add_argument("rules", help="rules file (markdown / pasted page text) or a bundled id, e.g. cortensor-hackathon-5")
    ap.add_argument("project", nargs="?", default=".", help="project folder (default: current folder)")
    ap.add_argument("--lang", choices=("en", "de"), default=os.environ.get("PREJUDGE_LANG", "en"), help="report language")
    ap.add_argument("--config", default=None, help="OpenMinion agents.json (default: SDK project default)")
    ap.add_argument("--agent", default=None, help="configured OpenMinion agent id")
    ap.add_argument("--no-skeptic", action="store_true", help="skip the skeptic subagent")
    ap.add_argument("--fix", action="store_true", help="let a fixer agent draft missing docs (asks before each file)")
    ap.add_argument("--yes", action="store_true", help="auto-approve file creation in --fix mode")
    ap.add_argument("--out", default=None, help="report folder (default: PROJECT/.prejudge)")
    ap.add_argument("--timeout", type=float, default=600, help="seconds per agent turn")
    ap.add_argument("--fail-under", type=float, default=None, help="exit 3 if the weighted score is below this (CI gate)")
    ap.add_argument("--version", action="version", version=f"prejudge {__version__}")
    a = ap.parse_args(argv)

    rules = parse_rules(_load_rules(a.rules))
    if not rules.criteria:
        print("No rubric found. Add lines like 'Working functionality: 25%' to the rules file.", file=sys.stderr)
        return 2
    ws = Workspace(a.project)

    from . import pipeline, report  # imported late so --help stays fast

    try:
        result = pipeline.run(
            ws, rules, config_path=a.config, agent_id=a.agent, lang=a.lang,
            skeptic=not a.no_skeptic, fix=a.fix, approve=_console_approval(a.yes), timeout=a.timeout,
        )
    except ProviderError as exc:
        print(f"model provider failed: {exc.code}: {exc.message}\nRun `openminion doctor --check-turn` to check your provider.", file=sys.stderr)
        return 1
    except TurnTimeoutError as exc:
        print(f"agent turn timed out: {exc}. Try --timeout 900 or a faster model.", file=sys.stderr)
        return 1

    out = Path(a.out) if a.out else ws.root / ".prejudge"
    out.mkdir(parents=True, exist_ok=True)
    md, htm = out / "PREJUDGE_REPORT.md", out / "prejudge_report.html"
    md.write_text(report.to_markdown(result, a.lang), encoding="utf-8")
    htm.write_text(report.to_html(result, a.lang), encoding="utf-8")

    print(f"\nScore: {result.total}/100")
    for cs in result.scorecard.criteria:
        print(f"  {cs.score:>2}/10  {cs.criterion}")
    for adj in result.adjustments:
        print(f"  adjusted {adj.criterion}: {adj.before} -> {adj.after} ({adj.reason[:80]})")
    if result.created_files:
        print("Created: " + ", ".join(result.created_files))
    print(f"Report: {md}\n        {htm}")
    if a.fail_under is not None and result.total < a.fail_under:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
