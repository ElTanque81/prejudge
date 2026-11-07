# Bug report drafts for OpenMinion

Ready to file as GitHub issues on `OpenMinion/openminion`. Searched open and closed issues on 2026-11-05;
no existing report found for A or B. C relates to #635, #497, #516. D is #464.

Both A and B reproduce with `python scripts/repro_openminion_findings.py` (no API key, local stub server).

---

## A

**Title:** [0.1.18] `Agent(instructions=...)` is missing from every provider request after the first tool call

**Version / environment:** openminion 0.1.18 (PyPI), Python 3.13.18 and 3.11, Linux, provider `openai`
(OpenAI-compatible) pointed at a local logging stub.

**Steps to reproduce**
1. `pip install openminion==0.1.18`
2. `python scripts/repro_openminion_findings.py` (from the PreJudge repo; standalone, ~90 lines)
   – one `Agent` with `instructions="MARKER_7f3a_INSTRUCTIONS: ..."`, one `@tool(min_scope="READ_ONLY")`,
   one `run()`.

**Expected:** the instruction text is present in every provider request of the agent loop.

**Actual:** present in request #1 (first agent step), absent from the full JSON of requests #3 and #4
(continuation steps carrying the tool result):

```
 #  tools          has tool result  instructions?
 1  list_items     False            True
 3  list_items     True             False
 4  list_items     True             False
```

**Impact:** any SDK agent that uses a tool answers its final step without its role, rules or output
format. Typed agents (`output_type`) are hit hardest because the format instructions live in
`instructions`.

**Pointer (unverified):** `Agent._build_payload` sends `override_system_prompt`; continuation steps may
rebuild the prompt from the default agent profile – e.g. `services/brain/post_execution/followup.py`
resolves `system_prompt` from `self._config.agents[resolve_default_agent_id(...)]`, which ignores both
the override and a selected `agent_id`.

**Workaround:** repeat the instructions in the user message (it survives as "Original request").

---

## B

**Title:** [0.1.18] `Agent(output_type=Model)` never sends the schema to the model; it is only validated afterwards

**Steps:** same script; `output_type` has a field named `zz_unique_field_name`.

**Expected:** the model is told the required JSON shape (schema block, `response_format`, or a forced
structured-output tool), as `output_type` suggests.

**Actual:** the field name appears in no agent-loop request (#1, #3, #4). `api/agent.py`
`_build_payload` has no `output_type` handling; `_coerce_output` validates the reply after the fact.
The structured-output example only works because it spells out the fields in `instructions`
(which then hits A when tools are used).

**Impact:** `AgentOutputValidationError` on models that would comply if they knew the schema; extra repair
turns on slow providers.

---

## E

**Title:** [0.1.18] Circular-pattern abort is returned as reply text; with `output_type` it becomes `AgentOutputValidationError`

**Observed:** Ollama `qwen2.5`, tool-using `Agent(output_type=Scorecard)`. The act loop stops with
`termination_reason=circular_pattern` (`modules/brain/loop/tools/engine.py`, `_handle_circular_pattern`)
and the user receives `openminion: [act] Repeated tool pattern did not produce a final answer.` as the
reply text. `_coerce_output` then raises `AgentOutputValidationError` (json_invalid).

**Expected:** a distinguishable signal (typed exception or `AgentRunResult.run_state`) so callers can
react (fall back, lower tool budget) instead of treating it as a formatting error.

**Note:** an `Agent` constructed with no tools still exposes internal decision tools
(`tool_request`, `respond`, `plan`, `research`, ...) to the model; worth documenting.

---

## F

**Title:** [0.1.18] No way to disable freshness classification; offline code review classified as "current finance" and refused

**Observed:** Ollama `qwen2.5`, `Agent(output_type=...)`, no web tools registered. User message contained
hackathon prize text ($COR, staking). Reply: "I couldn't provide a grounded current finance answer because
Missing live-data evidence for a freshness-sensitive answer.; Missing exact-date evidence ...".

**Expected:** an SDK/config switch, or automatic skip when the agent has no live-data tools.

## G

**Title:** [0.1.18] `forced_tools` does not set `tool_choice`; requests go out with `"auto"`

Docs suggest the listed tools are forced; the provider request carries `tool_choice: "auto"`, so models
may skip them. If it is meant as a prompt hint, the name and docs should say so.

---

## H

**Title:** [0.1.18] Ollama adapter does not set `num_ctx`; agent prompts beyond Ollama's default window are truncated from the start

**Where:** `modules/llm/providers/ollama/adapter.py` builds `payload["options"]` with temperature,
top_p, num_predict, stop. No `num_ctx`, no config field for it.

**Effect:** with Ollama's default window (4096 in current releases) the system prompt and tool contract
fall off the front of multi-step turns. qwen2.5:14b then answered with echoed tool output and with
OpenMinion's internal routing rules instead of the requested JSON.

**Expected:** `num_ctx` configurable per provider/model (and ideally a warning when the prompt estimate
exceeds it). Workaround: `OLLAMA_CONTEXT_LENGTH=12288` on the Ollama server.

---

## I

**Title:** [0.1.18] `ProviderError` drops the HTTP error body (`HTTP 421: HTTP 421`)

OpenAI-compatible provider (Otari, EU key sent to global host). The body
`{"detail":"This API key belongs to eu.api.otari.ai. Send the request there instead."}` is not shown by
`openminion setup` / `doctor`. Expected: include a truncated, secret-free excerpt of the body.

---

## C (context for #635 / #497 / #516)

One judge turn with two tool calls produced 8 provider requests; only 3–4 were the agent loop. The rest
are runtime-internal forced `submit_output` calls (memory candidate extraction with `items`, freshness
classification with `intent`). On Cortensor Portal this multiplies latency. Request: per-purpose call
counts in `stats` and an SDK-level switch to skip memory extraction for stateless agents.

## D (= #464, still present in 0.1.18)

A default `@tool` (no `min_scope`) gets `WRITE_SAFE` + `idempotent=False` → `write` / unknown
reversibility → "Policy confirmation required" returned as reply text. Declaring
`min_scope="READ_ONLY", idempotent=True` fixes it.
