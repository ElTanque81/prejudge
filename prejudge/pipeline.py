"""The PreJudge workflow: judge -> evidence gate -> skeptic subagent -> (optional) approved fixes.

OpenMinion building blocks used here:
  * Agent + @tool (workspace-confined read tools, forced first tool call)
  * output_type (Pydantic-validated Scorecard / SkepticReview)
  * sessions (one durable session per project + rules, reused across runs)
  * subagent() delegation (bounded skeptic reviewer with its own timeout)
  * approval_callback (human approval before any file is created)
  * run stats (llm_calls / tokens / duration) for diagnostics
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from openminion import Agent, AgentOutputValidationError, subagent

from .models import CriterionScore, Scorecard, SkepticReview
from .repo_tools import Workspace, guard_create, make_read_tools, make_write_tool
from .rules import Rules, weighted_total
from . import history

LANG_NAME = {"de": "German", "en": "English"}

JUDGE_INSTRUCTIONS = """You are PreJudge, a strict but fair hackathon judge.
You score ONE project against the organizer's rubric before the team submits.
Rules:
- Inspect the project with the tools first (submission_scan, repo_tree, read_file, search_repo).
- Score every rubric criterion 0-10. Use the EXACT criterion names you are given.
- Every score above 5 needs evidence: a repo-relative path plus a short verbatim quote (one line) from that file.
  Copy the text only, WITHOUT the "12: " line number that read_file shows. Quotes are machine-verified;
  invented quotes lower the score.
- Before scoring, open every markdown file whose name or headings match a checklist item
  (e.g. BUILDING_BLOCKS.md, WHAT_WE_LEARNED.md, docs/*) and judge what is actually in it.
- gaps = what a judge would miss; fixes = concrete, small next actions (file + what to add).
- Never reward claims that are only promised ("coming soon", TODO).
- Write project_summary, gaps, fixes and top_risks in {lang}.
Return only the JSON object for the requested schema."""

SKEPTIC_INSTRUCTIONS = """You are a second, skeptical hackathon judge. You did not see the repo,
only the first judge's scorecard and the deterministic scan. Find scores that are too generous
(weak or unverified evidence, promises instead of working code) or too harsh.
Only challenge when you have a concrete reason. Write reasons in {lang}.
verdict must be one of: agree, too_generous, too_harsh. Return only JSON."""

FIXER_INSTRUCTIONS = """You help a hackathon team close submission gaps by creating MISSING documents only.
Create at most 3 files, only for checklist items that have no file or README section yet.
Put them in the project root with clear names (e.g. BUILDING_BLOCKS.md, WHAT_WE_LEARNED.md).
Never create a document about secrets or keys (those must be deleted, not documented) and never
a separate file for a video link (it belongs in the README).
Use create_doc for each missing file (one call per file). Content must be an honest skeleton:
headings from the organizer's checklist, facts you can verify in the repo, and clear TODO markers
for anything the team must fill in (results, video link, findings). Never invent results.
Write the documents in {lang}. Finish with one short sentence listing what you created."""


@dataclass
class StageStats:
    name: str
    seconds: float
    stats: dict = field(default_factory=dict)


@dataclass
class Adjustment:
    criterion: str
    before: int
    after: int
    reason: str


@dataclass
class JudgeResult:
    rules: Rules
    scorecard: Scorecard
    total: float
    previous: dict | None
    scan: dict
    verified: dict[str, list[bool]]
    adjustments: list[Adjustment]
    skeptic: SkepticReview | None
    created_files: list[str]
    stages: list[StageStats]
    session_id: str
    existing: set[str] = field(default_factory=set)


def session_id_for(ws: Workspace, rules: Rules) -> str:
    key = f"{ws.root}|{rules.title}".encode()
    slug = re.sub(r"[^a-z0-9]+", "-", ws.root.name.lower()).strip("-")[:24] or "project"
    return f"prejudge-{slug}-{hashlib.sha1(key).hexdigest()[:8]}"


def _schema(model) -> str:
    return json.dumps(model.model_json_schema(), separators=(",", ":"))


def with_contract(role: str, model, body: str) -> str:
    """Repeat role + JSON schema in the user message.

    Workaround for two OpenMinion 0.1.18 behaviors found while building this:
    Agent(instructions=...) is dropped from the system prompt on every step
    after the first tool call, and output_type is only validated after the
    fact (its schema is never sent to the model). The user message survives
    as "Original request", so the contract lives there too.
    """
    return f"{role}\n\nReturn ONE JSON object matching this JSON schema:\n{_schema(model)}\n\n{body}"


def _judge_prompt(rules: Rules, previous: dict | None, language: str, ws: Workspace | None = None) -> str:
    rubric = "\n".join(f"- {c.name} ({round(c.weight * 100)}%)" for c in rules.criteria)
    checklist = "\n".join(f"- {c}" for c in rules.checklist) or "- (none listed)"
    prev = ""
    if previous:
        prev = (
            "\nPrevious PreJudge run in this session scored: "
            + json.dumps(previous.get("scores", {}))
            + ". Re-check those gaps; do not copy old scores without looking.\n"
        )
    body = (
        f"Hackathon: {rules.title}\n\nRubric (use these exact names):\n{rubric}\n\n"
        f"Submission checklist:\n{checklist}\n{prev}\n"
        "Organizer rules text (excerpt):\n" + rules.raw[:4000]
    )
    if ws is not None:
        tree = "\n".join(ws.rel(p) for p in ws.files()[:120])
        body += (
            "\n\nProject files (only these exist; never cite any other path):\n" + tree
            + "\n\nKey project files, inlined (quote from these or from read_file output):\n"
            + ws.context_pack(rules.checklist)
        )
    return with_contract(JUDGE_INSTRUCTIONS.format(lang=language), Scorecard, body)


def _align(scorecard: Scorecard, rules: Rules) -> Scorecard:
    """Map model criterion names onto rubric names; add missing criteria as 0."""
    names = [c.name for c in rules.criteria]
    by_name: dict[str, CriterionScore] = {}
    for cs in scorecard.criteria:
        if cs.criterion in names:
            target = cs.criterion
        else:
            match = difflib.get_close_matches(cs.criterion, names, n=1, cutoff=0.4)
            if not match:
                continue
            target = match[0]
        if target not in by_name:
            by_name[target] = cs.model_copy(update={"criterion": target})
    for n in names:
        by_name.setdefault(n, CriterionScore(criterion=n, score=0, gaps=["not assessed by the model"]))
    return scorecard.model_copy(update={"criteria": [by_name[n] for n in names]})


def evidence_gate(ws: Workspace, scorecard: Scorecard) -> tuple[Scorecard, dict[str, list[bool]], list[Adjustment]]:
    """Verify every quote against the repo. High scores without verified evidence are capped at 5."""
    verified: dict[str, list[bool]] = {}
    adjustments: list[Adjustment] = []
    new: list[CriterionScore] = []
    for cs in scorecard.criteria:
        flags = [ws.verify_quote(e.path, e.quote) for e in cs.evidence]
        verified[cs.criterion] = flags
        if cs.score > 5 and not any(flags):
            invented = [e.path for e in cs.evidence if not ws.file_exists(e.path)]
            why = "evidence gate: cited file(s) do not exist: " + ", ".join(invented) if invented else "evidence gate: no verifiable quote"
            adjustments.append(Adjustment(cs.criterion, cs.score, 5, why))
            cs = cs.model_copy(update={"score": 5})
        new.append(cs)
    return scorecard.model_copy(update={"criteria": new}), verified, adjustments


def apply_skeptic(scorecard: Scorecard, review: SkepticReview) -> tuple[Scorecard, list[Adjustment]]:
    """Meet the skeptic halfway; round toward the skeptic so generous scores shrink."""
    challenges = {c.criterion: c for c in review.challenges}
    adjustments: list[Adjustment] = []
    new: list[CriterionScore] = []
    for cs in scorecard.criteria:
        ch = challenges.get(cs.criterion)
        if ch is None:
            m = difflib.get_close_matches(cs.criterion, list(challenges), n=1, cutoff=0.6)
            ch = challenges.get(m[0]) if m else None
        if ch is not None and ch.suggested_score != cs.score:
            mid = (cs.score + ch.suggested_score) / 2
            after = int(mid) if ch.suggested_score < cs.score else int(mid + 0.5)
            if after != cs.score:
                adjustments.append(Adjustment(cs.criterion, cs.score, after, f"skeptic: {ch.reason}"))
                cs = cs.model_copy(update={"score": after})
        new.append(cs)
    return scorecard.model_copy(update={"criteria": new}), adjustments


def _is_runtime_notice(text: str | None) -> bool:
    """OpenMinion reports loop aborts (e.g. "[act] repeated tool pattern") as plain reply text."""
    t = (text or "").strip().lower()
    return t.startswith("openminion: [") or "repeated tool pattern" in t


class StageFailed(RuntimeError):
    pass


def _run_typed(agent: Agent, prompt: str, stage: str, stages: list[StageStats], **kw):
    t0 = time.monotonic()
    try:
        res = agent.run(prompt, **kw)
    except AgentOutputValidationError as exc:
        if _is_runtime_notice(exc.raw_text):
            stages.append(StageStats(stage + " (runtime abort)", round(time.monotonic() - t0, 1), {}))
            raise StageFailed((exc.raw_text or "")[:200]) from exc
        # One repair attempt in the same session: show the model its own invalid output.
        try:
            res = agent.run(
                "Your previous answer did not match the JSON schema. Return ONLY the corrected JSON object.\n"
                f"Invalid answer:\n{(exc.raw_text or '')[:3000]}",
                **kw,
            )
        except AgentOutputValidationError as exc2:
            stages.append(StageStats(stage + " (invalid twice)", round(time.monotonic() - t0, 1), {}))
            raise StageFailed((exc2.raw_text or str(exc2))[:200]) from exc2
        stage += " (+repair)"
    stages.append(StageStats(stage, round(time.monotonic() - t0, 1), dict(res.stats)))
    return res


def run(
    ws: Workspace,
    rules: Rules,
    *,
    config_path: str | None = None,
    agent_id: str | None = None,
    lang: str = "en",
    skeptic: bool = True,
    fix: bool = False,
    approve: Callable[[str, dict, str], bool] | None = None,
    log: Callable[[str], None] = print,
    timeout: float = 600,
) -> JudgeResult:
    language = LANG_NAME.get(lang, "English")
    sid = session_id_for(ws, rules)
    previous = history.last_for(ws.root, rules.title)
    scan = ws.scan()
    stages: list[StageStats] = []
    skeptic_review: SkepticReview | None = None
    adjustments: list[Adjustment] = []
    created: list[str] = []

    with Agent(
        name="prejudge",
        config_path=config_path,
        agent_id=agent_id,
        instructions=JUDGE_INSTRUCTIONS.format(lang=language),
        tools=make_read_tools(ws),
        forced_tools=["submission_scan"],
        output_type=Scorecard,
        session_id=sid,
    ) as judge:
        log(f"[1/3] judging {ws.root.name} against {len(rules.criteria)} rubric criteria (session {sid})")
        prompt = _judge_prompt(rules, previous, language, ws)
        try:
            res = _run_typed(judge, prompt, "judge", stages, timeout_seconds=timeout)
            raw_card = res.output
        except StageFailed as exc:
            # Fallback: same contract, no tools. Everything the judge needs is already inlined.
            log(f"  judge aborted ({str(exc)[:80]}); retrying without tools from the inlined files")
            with Agent(
                name="prejudge-answer-only",
                config_path=config_path,
                agent_id=agent_id,
                instructions=JUDGE_INSTRUCTIONS.format(lang=language),
                output_type=Scorecard,
                session_id=sid + "-answer-only",
            ) as plain:
                res = _run_typed(
                    plain,
                    prompt + "\n\nNo tools are available in this turn. Score from the inlined files and file list only.",
                    "judge (fallback, no tools)",
                    stages,
                    timeout_seconds=timeout,
                )
                raw_card = res.output
        card = _align(raw_card, rules)
        card, verified, gate_adj = evidence_gate(ws, card)
        adjustments += gate_adj

        if skeptic:
            log("[2/3] skeptic subagent reviews the scorecard")
            reviewer = subagent(
                judge,
                name="skeptic",
                instructions=SKEPTIC_INSTRUCTIONS.format(lang=language),
                output_type=SkepticReview,
                timeout_seconds=int(timeout),
            )
            payload = {
                "rubric": [{"name": c.name, "weight": c.weight} for c in rules.criteria],
                "scorecard": card.model_dump(),
                "evidence_verified": verified,
                "deterministic_scan": {k: v for k, v in scan.items() if k != "markdown_headings"},
            }
            try:
                prompt = with_contract(SKEPTIC_INSTRUCTIONS.format(lang=language), SkepticReview, json.dumps(payload, ensure_ascii=False))
                sres = _run_typed(reviewer, prompt, "skeptic", stages, timeout_seconds=timeout)
                skeptic_review = sres.output
                card, sk_adj = apply_skeptic(card, skeptic_review)
                adjustments += sk_adj
            except StageFailed as exc:
                log(f"  skeptic skipped: {str(exc)[:120]}")

    if fix:
        log("[3/3] fixer drafts missing docs (each file needs your approval)")
        gaps = [g for cs in card.criteria for g in cs.gaps]
        with Agent(
            name="prejudge-fixer",
            config_path=config_path,
            agent_id=agent_id,
            instructions=FIXER_INSTRUCTIONS.format(lang=language),
            tools=make_read_tools(ws) + make_write_tool(ws, created),
            session_id=sid + "-fix",
        ) as fixer:
            prompt = FIXER_INSTRUCTIONS.format(lang=language) + "\n\n" + (
                "Organizer checklist:\n" + "\n".join(f"- {c}" for c in rules.checklist)
                + "\n\nGaps found by the judge:\n" + "\n".join(f"- {g}" for g in gaps[:30])
                + "\n\nExisting markdown headings:\n" + "\n".join(scan["markdown_headings"][:40])
            )
            t0 = time.monotonic()
            def gated(tool_name: str, args: dict, approval_id: str) -> bool:
                # Duplicates and over-limit calls are denied without bothering the human.
                reason = guard_create(ws, created, args) if tool_name == "create_doc" else None
                if reason:
                    log(f"  auto-denied {tool_name} {args.get('path')}: {reason}")
                    return False
                return approve(tool_name, args, approval_id) if approve else False

            fres = fixer.run(prompt, timeout_seconds=timeout, approval_callback=gated)
            stages.append(StageStats("fixer", round(time.monotonic() - t0, 1), dict(fres.stats)))

    scores = {cs.criterion: cs.score for cs in card.criteria}
    total = weighted_total(rules, scores)
    history.append(ws.root, rules.title, total, scores, sid)
    existing = {e.path for cs in card.criteria for e in cs.evidence if ws.file_exists(e.path)}
    return JudgeResult(rules, card, total, previous, scan, verified, adjustments, skeptic_review, created, stages, sid, existing)
