# What We Learned

Actionable feedback for OpenMinion and Cortensor, ordered by impact on a new builder.

1. **Keep `instructions` on every step of a turn.** An SDK user reads `Agent(instructions=...)` as "the
   system prompt". Losing it after the first tool call breaks output format and role exactly at the final
   answer. A regression test could assert that the instruction text is present in every provider request
   of a multi-step turn.

2. **Send the `output_type` schema to the model** (as a system block or native `response_format` /
   forced `submit_output` tool where the provider supports it). Today validation is strict but the model is
   never told the contract, so typed agents only work if the author duplicates the schema by hand.

3. **Make read tools easy to declare.** A `@tool(read_only=True)` alias, or inferring read-only for
   functions without side effects in examples, would avoid the first-run "Policy confirmation required"
   wall (#464). At minimum, the README snippet should show `min_scope="READ_ONLY"`.

4. **Expose the internal call budget.** `stats` already counts calls; a per-purpose breakdown
   (`agent_loop`, `memory_extract`, `freshness`) and an SDK switch to disable memory extraction for
   stateless agents would let Portal users trade features for latency.

5. **The approval callback is the right shape.** `(tool_name, args, approval_id)` was enough to build a
   preview-and-confirm CLI in a few lines. An `approval_callback` example in `examples/sdk/` (today it lives
   only in `submitted_stream.py` via `resolve_approval`) would help.

6. **Builder workflow idea for Cortensor:** hackathon rules with explicit weights are machine-readable.
   PreJudge can run in CI on every PR to the submission repo and post the deterministic checks (README,
   video link, findings notes, committed secrets) before human review.
