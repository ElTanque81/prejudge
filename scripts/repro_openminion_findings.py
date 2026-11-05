"""Reproduce findings A and B against openminion (no API key needed).

A: Agent(instructions=...) is missing from every provider request after the first tool call.
B: Agent(output_type=...) schema never reaches the model.

Starts a logging OpenAI-compatible stub on localhost, points a fresh OpenMinion
home at it, runs one Agent turn with one read-only tool, then prints, per
provider request, whether the instruction marker and the output schema field
name appear anywhere in the request JSON.

    python scripts/repro_openminion_findings.py
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REQUESTS: list[dict] = []
MARKER = "MARKER_7f3a_INSTRUCTIONS"
SCHEMA_FIELD = "zz_unique_field_name"


class Stub(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append(body)
        tools = body.get("tools") or []
        has_result = any(m.get("role") == "tool" for m in body.get("messages", []))
        choice = body.get("tool_choice")
        if isinstance(choice, dict):  # runtime-internal forced structured call
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "s1", "type": "function",
                   "function": {"name": choice["function"]["name"], "arguments": "{}"}}]}
        elif tools and not has_result:
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function",
                   "function": {"name": tools[0]["function"]["name"], "arguments": "{}"}}]}
        else:
            msg = {"role": "assistant", "content": json.dumps({SCHEMA_FIELD: "ok"})}
        data = json.dumps({"id": "x", "object": "chat.completion", "model": body.get("model"),
                           "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = Path(tempfile.mkdtemp(prefix="om-repro-"))
    subprocess.run(["openminion", "config", "init", "--provider", "openai"], cwd=home, check=True,
                   capture_output=True, env={**os.environ, "OPENMINION_HOME": str(home)})
    cfg = home / ".openminion/agents.json"
    data = json.loads(cfg.read_text())
    data["providers"]["openai"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="sk-stub")
    cfg.write_text(json.dumps(data))

    from pydantic import BaseModel
    from openminion import Agent, __version__, tool

    class Out(BaseModel):
        zz_unique_field_name: str

    @tool(min_scope="READ_ONLY", idempotent=True)
    def list_items() -> str:
        """List items."""
        return "a, b"

    with Agent(config_path=str(cfg), instructions=f"{MARKER}: call list_items, then answer.",
               tools=[list_items], output_type=Out, session_id="repro") as agent:
        result = agent.run("What items are there?")

    print(f"openminion {__version__}; result.output={result.output!r}")
    print(f"{'#':>2}  {'tools':<28} {'has tool result':<16} {'instructions?':<14} schema field?")
    for i, body in enumerate(REQUESTS):
        blob = json.dumps(body)
        tools = ",".join(t["function"]["name"] for t in body.get("tools") or []) or "-"
        has_result = any(m.get("role") == "tool" for m in body.get("messages", []))
        print(f"{i:>2}  {tools:<28} {str(has_result):<16} {str(MARKER in blob):<14} {SCHEMA_FIELD in blob}")
    print("\nExpected: instructions? = True on every agent-loop request (tools=list_items).")
    print("Expected: schema field? = True on agent-loop requests when output_type is set.")
    server.shutdown()


if __name__ == "__main__":
    main()
