"""Scripted OpenAI-compatible chat server for offline end-to-end tests.

It is NOT a model. It plays the three PreJudge roles deterministically so the
real OpenMinion runtime (tool registry, policy, sessions, output validation,
subagents, approvals) can be exercised without credentials.

Usage: python tests/mock_model.py PORT [LOGFILE]
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], "a") if len(sys.argv) > 2 else None

SCORECARD = {
    "project_summary": "Sample agent that summarizes text with the OpenMinion SDK.",
    "criteria": [
        {"criterion": "Working functionality and useful outcome", "score": 7,
         "evidence": [{"path": "README.md", "quote": "python agent.py"}], "gaps": ["No sample output shown"], "fixes": ["Add an example output section to README.md"]},
        {"criterion": "Meaningful OpenMinion SDK usage", "score": 8,
         "evidence": [{"path": "agent.py", "quote": "this quote does not exist"}], "gaps": [], "fixes": []},
        {"criterion": "Reproducibility and demo quality", "score": 4, "evidence": [], "gaps": ["No demo video link"], "fixes": ["Add a 1-5 minute video link to README.md"]},
        {"criterion": "Building block findings", "score": 2, "evidence": [], "gaps": ["No Building Blocks Tested note"], "fixes": ["Create BUILDING_BLOCKS.md"]},
    ],
    "top_risks": ["Demo video missing"],
}
SKEPTIC = {"verdict": "too_generous", "challenges": [
    {"criterion": "Working functionality and useful outcome", "suggested_score": 5, "reason": "Only a run command, no proof of output."}]}


def _schema_props(tool: dict) -> dict:
    return (tool.get("function", {}).get("parameters") or {}).get("properties") or {}


def _tool_call(name: str, args: dict, cid: str = "call_1") -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def respond(body: dict) -> dict:
    msgs = body.get("messages", [])
    tools = body.get("tools") or []
    names = [t["function"]["name"] for t in tools]
    # Like a real model, look at the whole conversation (instructions may only survive in the user turn).
    system = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") in {"system", "user"})
    tool_results = [m for m in msgs if m.get("role") == "tool"]
    forced = (body.get("tool_choice") or {}) if isinstance(body.get("tool_choice"), dict) else {}
    forced_name = forced.get("function", {}).get("name")

    # Runtime-internal structured calls (memory candidates, freshness, etc.).
    if forced_name and forced_name not in {"submission_scan", "create_doc"}:
        props = _schema_props(next(t for t in tools if t["function"]["name"] == forced_name))
        if "criteria" in props:
            return _tool_call(forced_name, SCORECARD)
        if "verdict" in props:
            return _tool_call(forced_name, SKEPTIC)
        if "items" in props:
            return _tool_call(forced_name, {"items": []})
        if "intent" in props:
            return _tool_call(forced_name, {"intent": "local", "confidence": 0.9})
        return _tool_call(forced_name, {})

    if "skeptical hackathon judge" in system:
        return {"role": "assistant", "content": json.dumps(SKEPTIC)}
    if "MISSING documents" in system:
        if "create_doc" in names and not tool_results:
            return _tool_call("create_doc", {"path": "BUILDING_BLOCKS.md",
                                             "content": "# Building Blocks Tested\n\n- TODO: list blocks"})
        return {"role": "assistant", "content": "Created BUILDING_BLOCKS.md."}
    if "You are PreJudge" in system and "LOOP_TRIGGER.md" in system and "read_file" in names:
        # Simulates the runtime's circular-pattern abort text for the tool-using judge.
        return {"role": "assistant", "content": "[act] Repeated tool pattern did not produce a final answer."}
    if "You are PreJudge" in system:
        done = len(tool_results)
        if done == 0 and "submission_scan" in names:
            return _tool_call("submission_scan", {})
        if done == 1 and "read_file" in names:
            return _tool_call("read_file", {"path": "README.md"}, "call_2")
        return {"role": "assistant", "content": json.dumps(SCORECARD)}
    return {"role": "assistant", "content": "ok"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msg = respond(body)
        if LOG:
            LOG.write(json.dumps({"tools": [t["function"]["name"] for t in body.get("tools") or []],
                                  "tool_choice": body.get("tool_choice"), "reply": msg}) + "\n")
            LOG.flush()
        out = {"id": "mock", "object": "chat.completion", "model": body.get("model"),
               "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
