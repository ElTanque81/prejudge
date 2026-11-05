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

## C (context for #635 / #497 / #516)

One judge turn with two tool calls produced 8 provider requests; only 3–4 were the agent loop. The rest
are runtime-internal forced `submit_output` calls (memory candidate extraction with `items`, freshness
classification with `intent`). On Cortensor Portal this multiplies latency. Request: per-purpose call
counts in `stats` and an SDK-level switch to skip memory extraction for stateless agents.

## D (= #464, still present in 0.1.18)

A default `@tool` (no `min_scope`) gets `WRITE_SAFE` + `idempotent=False` → `write` / unknown
reversibility → "Policy confirmation required" returned as reply text. Declaring
`min_scope="READ_ONLY", idempotent=True` fixes it.
