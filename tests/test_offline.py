"""Offline tests: deterministic parts plus a full SDK run against the scripted mock model."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from prejudge.models import CriterionScore, Evidence, Scorecard, SkepticReview, Challenge
from prejudge.pipeline import apply_skeptic, evidence_gate, _align
from prejudge.repo_tools import Workspace
from prejudge.rules import parse_rules, weighted_total

ROOT = Path(__file__).resolve().parents[1]
RULES = (ROOT / "prejudge/rules_library/cortensor-hackathon-5.md").read_text()


def test_parse_markdown_rules():
    r = parse_rules(RULES)
    assert [round(c.weight * 100) for c in r.criteria] == [25, 20, 20, 25, 10]
    assert r.weights_ok and len(r.checklist) == 5


def test_parse_pasted_page_text():
    r = parse_rules((ROOT / "tests/fixtures/pasted_page.txt").read_text())
    assert len(r.criteria) == 5 and r.weights_ok
    assert r.checklist[0].startswith("A public source repository")
    assert len(r.checklist) == 5


def test_weighted_total():
    r = parse_rules(RULES)
    assert weighted_total(r, {c.name: 10 for c in r.criteria}) == 100.0
    assert weighted_total(r, {}) == 0.0


@pytest.fixture()
def project(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(ROOT / "examples/sample-project", dst)
    return dst


def test_workspace_confinement(project):
    ws = Workspace(project)
    with pytest.raises(PermissionError):
        ws.resolve("../../etc/passwd")


def test_scan_finds_secret_and_missing_video(project):
    s = Workspace(project).scan()
    assert s["readme_has_setup"] and s["readme_has_run"]
    assert s["demo_video_links"] == []
    assert any(x["kind"] == "openai_key" for x in s["possible_secrets"])


def test_evidence_gate_caps_unverified_high_scores(project):
    ws = Workspace(project)
    card = Scorecard(project_summary="x", criteria=[
        CriterionScore(criterion="A", score=9, evidence=[Evidence(path="README.md", quote="python agent.py")]),
        CriterionScore(criterion="B", score=9, evidence=[Evidence(path="agent.py", quote="invented line")]),
        CriterionScore(criterion="C", score=4),
    ])
    out, verified, adj = evidence_gate(ws, card)
    assert [c.score for c in out.criteria] == [9, 5, 4]
    assert verified == {"A": [True], "B": [False], "C": []}
    assert [a.criterion for a in adj] == ["B"]


def test_quote_verification_tolerates_copied_prefixes(project):
    ws = Workspace(project)
    assert ws.verify_quote("README.md", "14: python agent.py notes.txt")
    assert ws.verify_quote("README.md:14", "`python agent.py notes.txt`")
    assert ws.verify_quote("README.md", "# Summarizer Agent\nSummarizes a text file ... OpenMinion SDK.")
    assert not ws.verify_quote("README.md", "This project uses Cortensor Portal for inference")
    assert not ws.verify_quote("README.md", "a")


def test_context_pack_inlines_readme(project):
    pack = Workspace(project).context_pack(["A Building Blocks Tested note"])
    assert pack.startswith("=== README.md ===") and "python agent.py" in pack


def test_skeptic_meets_halfway_rounding_toward_skeptic():
    card = Scorecard(project_summary="x", criteria=[CriterionScore(criterion="A", score=7), CriterionScore(criterion="B", score=3)])
    review = SkepticReview(verdict="too_generous", challenges=[
        Challenge(criterion="A", suggested_score=4, reason="weak"),
        Challenge(criterion="B", suggested_score=6, reason="harsh"),
    ])
    out, adj = apply_skeptic(card, review)
    assert [c.score for c in out.criteria] == [5, 5]
    assert len(adj) == 2


def test_align_maps_names_and_fills_missing():
    r = parse_rules(RULES)
    card = Scorecard(project_summary="x", criteria=[CriterionScore(criterion="Building block findings", score=3)])
    out = _align(card, r)
    assert [c.criterion for c in out.criteria] == [c.name for c in r.criteria]
    assert out.criteria[3].score == 3 and out.criteria[0].score == 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("openminion") is None, reason="openminion CLI not installed")
def test_end_to_end_with_mock_model(project, tmp_path):
    """Real OpenMinion runtime, scripted model: tools, policy, sessions, subagent, approval."""
    port = _free_port()
    server = subprocess.Popen([sys.executable, str(ROOT / "tests/mock_model.py"), str(port)])
    try:
        time.sleep(0.8)
        home = tmp_path / "home"
        home.mkdir()
        env_cmd = ["openminion", "config", "init", "--provider", "openai"]
        subprocess.run(env_cmd, cwd=home, check=True, capture_output=True, env={**__import__("os").environ, "OPENMINION_HOME": str(home)})
        cfg = home / ".openminion/agents.json"
        data = json.loads(cfg.read_text())
        data["providers"]["openai"].update(base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-mock")
        cfg.write_text(json.dumps(data))

        from prejudge.cli import main
        code = main(["cortensor-hackathon-5", str(project), "--config", str(cfg), "--fix", "--yes", "--lang", "de", "--fail-under", "90"])
        assert code == 3  # below the CI gate
        report = (project / ".prejudge/PREJUDGE_REPORT.md").read_text()
        assert "PreJudge-Bericht" in report and "evidence gate" in report
        assert (project / "BUILDING_BLOCKS.md").is_file()
        assert "prejudge_report.html" in {p.name for p in (project / ".prejudge").iterdir()}
    finally:
        server.terminate()
