"""Workspace-confined repo tools exposed to the model through ``openminion.tool``.

Read tools are declared ``min_scope="READ_ONLY"`` so OpenMinion's action
policy lets them run without a prompt. The single write tool keeps the default
write scope and is non-idempotent, so every call goes through the approval
callback (human in the loop).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from openminion import tool

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".openminion", "dist", "build", ".mypy_cache", ".pytest_cache", ".next", ".prejudge"}
TEXT_EXT = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".sh", ".rs", ".go", ".sol", ".html", ".css", ".env", ".example", ""}
MAX_READ = 3500

VIDEO_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|loom\.com|vimeo\.com|drive\.google\.com|streamable\.com|x\.com|twitter\.com)/\S+", re.I)
SECRET_PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "hex_private_key": re.compile(r"(?i)private[_ ]?key\s*[:=]\s*['\"]?(?:0x)?[0-9a-f]{64}"),
}


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"project folder not found: {self.root}")

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel.strip().lstrip("/")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"path escapes project: {rel}")
        return p

    def files(self) -> list[Path]:
        out: list[Path] = []
        for p in sorted(self.root.rglob("*")):
            parts = p.relative_to(self.root).parts
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in parts):
                continue
            if p.is_file():
                out.append(p)
        return out

    def text_files(self) -> list[Path]:
        return [p for p in self.files() if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 400_000]

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def read(self, p: Path) -> str:
        return p.read_text(encoding="utf-8", errors="replace")

    # ---------- deterministic signals (also used by the report) ----------
    def scan(self) -> dict:
        files = self.files()
        names = [self.rel(p) for p in files]
        readme = next((p for p in files if p.parent == self.root and p.name.lower().startswith("readme")), None)
        readme_text = self.read(readme) if readme else ""
        low = readme_text.lower()
        secrets: list[dict] = []
        for p in self.text_files():
            if p.name.endswith((".example", ".sample")):
                continue
            text = self.read(p)
            for kind, rx in SECRET_PATTERNS.items():
                m = rx.search(text)
                if m:
                    line = text[: m.start()].count("\n") + 1
                    secrets.append({"file": self.rel(p), "line": line, "kind": kind})
        if any(n == ".env" or n.endswith("/.env") for n in names):
            secrets.append({"file": ".env", "line": 0, "kind": "env_file_committed"})
        headings = []
        for p in self.text_files():
            if p.suffix.lower() == ".md":
                for ln in self.read(p).splitlines():
                    if ln.startswith("#"):
                        headings.append(f"{self.rel(p)}: {ln.strip()[:90]}")
        return {
            "file_count": len(files),
            "readme": self.rel(readme) if readme else None,
            "readme_has_setup": bool(re.search(r"install|setup|getting started|quick ?start|einrichtung", low)),
            "readme_has_run": bool(re.search(r"\brun\b|usage|python -m|npm (run|start)|ausführen", low)),
            "demo_video_links": sorted(set(VIDEO_RE.findall(readme_text)))[:5],
            "license": next((n for n in names if Path(n).name.upper().startswith(("LICENSE", "LICENCE"))), None),
            "has_tests": any(re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|test_.*\.py$|\.test\.[jt]sx?$", n) for n in names),
            "possible_secrets": secrets[:20],
            "markdown_headings": headings[:80],
        }

    def verify_quote(self, rel: str, quote: str) -> bool:
        """True when the file exists and the quote appears in it.

        Tolerates what models typically add when copying from read_file / search_repo:
        "12: " line-number prefixes, "path:12:" prefixes, surrounding quotes or backticks,
        "..." elisions and multi-line quotes (enough of the lines must match).
        """
        try:
            p = self.resolve(rel.split(":")[0])
        except PermissionError:
            return False
        if not p.is_file():
            return False
        if not quote.strip():
            return True
        norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
        text = norm(self.read(p))
        lines = []
        for ln in quote.splitlines():
            ln = re.sub(r"^\s*(?:[\w./-]+:)?\d+:\s?", "", ln)
            ln = ln.strip().strip("`\"'“”‘’").strip()
            if ln:
                lines.append(ln)
        fragments = [norm(f) for ln in lines for f in re.split(r"\.\.\.|…", ln)]
        fragments = [f for f in fragments if len(f) >= 6]
        if not fragments:
            return False
        hits = sum(1 for f in fragments if f[:200] in text)
        return hits == len(fragments) if len(fragments) <= 2 else hits >= (len(fragments) + 1) // 2


def make_read_tools(ws: Workspace) -> list:
    """Build read-only tools bound to one project folder."""

    @tool(name="repo_tree", min_scope="READ_ONLY", idempotent=True)
    def repo_tree(max_entries: int = 200) -> str:
        """List project files (repo-relative paths with sizes). Skips .git, venvs and node_modules."""
        rows = [f"{ws.rel(p)} ({p.stat().st_size} B)" for p in ws.files()[: max(1, min(max_entries, 400))]]
        return "\n".join(rows) or "(empty project)"

    @tool(name="read_file", min_scope="READ_ONLY", idempotent=True)
    def read_file(path: str, start_line: int = 1) -> str:
        """Read a project text file from start_line. Output is truncated; call again with a later start_line for more."""
        p = ws.resolve(path)
        if not p.is_file():
            return f"not found: {path}"
        lines = ws.read(p).splitlines()
        start = max(1, start_line)
        out, size = [], 0
        for i, ln in enumerate(lines[start - 1 :], start=start):
            row = f"{i}: {ln}"
            size += len(row) + 1
            if size > MAX_READ:
                out.append(f"... truncated, continue with start_line={i}")
                break
            out.append(row)
        return "\n".join(out) or "(empty file)"

    @tool(name="search_repo", min_scope="READ_ONLY", idempotent=True)
    def search_repo(pattern: str, max_hits: int = 30) -> str:
        """Case-insensitive regex search across project text files. Returns path:line: text."""
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        hits: list[str] = []
        for p in ws.text_files():
            for i, ln in enumerate(ws.read(p).splitlines(), start=1):
                if rx.search(ln):
                    hits.append(f"{ws.rel(p)}:{i}: {ln.strip()[:160]}")
                    if len(hits) >= max(1, min(max_hits, 80)):
                        return "\n".join(hits)
        return "\n".join(hits) or "no matches"

    @tool(name="submission_scan", min_scope="READ_ONLY", idempotent=True)
    def submission_scan() -> str:
        """Deterministic checks: README setup/run sections, demo video links, license, tests, possible committed secrets, markdown headings."""
        return json.dumps(ws.scan(), indent=1)

    return [repo_tree, read_file, search_repo, submission_scan]


def make_write_tool(ws: Workspace, written: list[str]) -> list:
    """One create-only write tool. Non-idempotent write scope => OpenMinion asks for approval on every call."""

    @tool(name="create_doc", min_scope="WRITE_SAFE", idempotent=False)
    def create_doc(path: str, content: str) -> str:
        """Create a NEW markdown file in the project (never overwrites). Use for missing submission docs."""
        p = ws.resolve(path)
        if p.suffix.lower() not in {".md", ".txt"}:
            return "refused: only .md or .txt files may be created"
        if p.exists():
            return f"refused: {path} already exists"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(ws.rel(p))
        return f"created {ws.rel(p)} ({len(content)} chars)"

    return [create_doc]
