# Building Blocks Tested

OpenMinion version: **0.1.18** (PyPI), Python 3.13 and 3.11, Linux.
How it was tested: the full PreJudge workflow through the real OpenMinion runtime against a scripted
OpenAI-compatible server (`tests/mock_model.py`), plus request-level probes that log exactly what the
runtime sends to the provider. Real-model runs: see the table at the end (to be filled with the demo run).

| Block | Used for | Result |
|---|---|---|
| `Agent` + `@tool` | 4 read tools + 1 write tool bound to a project folder via closures | ✅ works. Tools created inside a factory function register and unregister cleanly per run. |
| Tool risk / permissions | read tools `min_scope="READ_ONLY"`, write tool default scope | ⚠️ works once declared. The **default** `@tool` (no `min_scope`) is classified `write` / unknown reversibility and every call stops at "Policy confirmation required" – also for pure lookups. Known upstream as #464. |
| `forced_tools` | `submission_scan` must run first | ✅ honored (`tool_choice` forces the function on step 1). |
| `output_type` (Pydantic) | `Scorecard`, `SkepticReview` | ⚠️ validation works and recovers JSON wrapped in prose / the `openminion: ` prefix (#567). But the **schema is never sent to the model** – see finding B. |
| `AgentOutputValidationError` | one repair turn in the same session with the invalid text | ✅ `raw_text` is available and useful for repair. |
| `instructions=` | judge / skeptic / fixer roles | ❌ **dropped after the first tool call** – continuation steps carry neither the system prompt nor the text anywhere else. See finding A. |
| Sessions | durable `session_id` per project + rules | ✅ reused across runs; separate `-fix` session for the fixer. |
| `subagent()` delegation | skeptic reviewer with own `output_type` + timeout | ✅ runs on the parent runtime; output validated. |
| `approval_callback` on `Agent.run` | human y/N before `create_doc` | ✅ called with `(tool_name, args, approval_id)`; deny stops the write, allow runs it. Earlier bug #270 is fixed in 0.1.18. |
| `AgentRunResult.stats` | per-stage LLM calls / tokens / seconds in the report | ✅ present. Shows the internal call overhead (finding C). |
| `ProviderError`, `TurnTimeoutError` | CLI error handling | ✅ importable from the root package as documented. |
| Memory (runtime) | not used directly | ℹ️ still runs on every turn (candidate extraction call), see finding C. |
| Skills, tasks, scheduling | not used | – PreJudge is one bounded workflow; adding them would be complexity for show. |

## Findings

**A. `Agent(instructions=...)` disappears after the first tool step (likely new).**
The first request carries the instructions in the `[IDENTITY]` system block. Every later request in the same
turn (continuation with tool results) contains no trace of them – checked by searching the full request JSON
for a marker string. Effect: role, rules and output format are lost exactly when the model writes its answer.
Workaround in PreJudge: repeat role + schema in the user message, which survives as "Original request"
(`pipeline.with_contract`). Repro: `docs/BUG_REPORTS.md#a`.

**B. `output_type` schema is not part of the prompt (likely new / docs gap).**
`Agent._build_payload` sends `override_system_prompt`, tools and overrides; `output_type` is only used in
`_coerce_output` after the reply. The SDK example works because it spells the fields out in `instructions`
– which then hits finding A on any tool-using agent. Workaround: PreJudge sends
`model_json_schema()` in the user message.

**C. Hidden LLM calls per turn.**
A judge turn with 2 tool calls made **8** provider requests: memory candidate extraction (`submit_output` with
`items`) and freshness classification (`submit_output` with `intent`) run around the agent loop. On a slow
provider such as Cortensor Portal this multiplies latency. Related upstream: #635, #497, #516.

**D. Default `@tool` needs confirmation (known, #464).** README quick start `lookup_order` has the same shape.

## Real-model runs

Mac mini, Ollama, `llama3.1` (8B), PreJudge v0.1.0 judging its own repo (before the context-pack change):

| Provider / model | Stage | Seconds | LLM calls | Tokens in/out | Valid JSON first try? | Notes |
|---|---|---|---|---|---|---|
| Ollama llama3.1 8B | judge | 87.0 | 3 | 8067 / 1065 | yes | Called only the forced `submission_scan`, read no file, then answered. |
| Ollama llama3.1 8B | skeptic | 61.7 | 3 | 8017 / 470 | yes | Valid `SkepticReview`. |
| Ollama llama3.1 8B | fixer (sample project) | – | – | – | n/a | Proposed 5 docs through `create_doc`; approval prompt shown for each; 3 approved, 2 denied (a doc *about* a committed key, a separate video-link file). |

**Evidence gate on a real model:** 5 of 5 cited quotes were invented, 3 of them from files that do not
exist (`openminion_sdk_usage.py`, `demo_video.mp4`, `building_blocks_tested.txt`). All were caught and
capped; without the gate the self-score would have been inflated by 8.5 points (6→5 at 25 %, 8→5 at 20 %).
Response in v0.1.1: the judge prompt now carries the real file list and the README plus checklist docs
inline (`Workspace.context_pack`), and the report labels citations of non-existent files separately.

| Provider / model | Stage | Seconds | LLM calls | Tokens in/out | Notes |
|---|---|---|---|---|---|
| TODO (re-run after v0.1.1) | judge | | | | |

**Fixer loop with qwen2.5 (Ollama):** after an approved `create_doc`, the fixer proposed the same file
again with a fresh approval id, repeatedly. Consistent with finding A (instructions and plan context gone
after the first tool step). Response in PreJudge: `guard_create` denies duplicates and anything past
3 files inside the approval callback, before the human is asked. Same run: qwen2.5 sent tool arguments
with double-escaped newlines (`\\n`); `create_doc` repairs them.
