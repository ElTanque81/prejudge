# PreJudge

**Judge your hackathon project with the organizer's own rubric — before the real judges do.**

PreJudge is a small agent built on the [OpenMinion](https://github.com/OpenMinion/openminion) 0.1 Python SDK.
Give it a hackathon's rules (a markdown file, or the DoraHacks page text pasted into a file) and your project
folder. It reads your repo with workspace-confined tools, scores every rubric criterion with **machine-verified
evidence**, lets a **skeptic subagent** challenge generous scores, and writes a report with concrete fixes.
Optionally a fixer agent drafts missing submission docs — **only after you approve each file**.

Built for Cortensor Hackathon #5. The rules for it ship with the tool:

```bash
prejudge cortensor-hackathon-5 path/to/your-project
```

> 🇩🇪 Deutsch: siehe [Kurzanleitung auf Deutsch](#kurzanleitung-auf-deutsch). Reports gibt es auf Englisch und Deutsch (`--lang de`).

## Why

Every hackathon page has a checklist and weighted criteria, and most submissions still lose points on things
that were knowable up front: no demo link, no setup steps, a findings note that was never written, a key
committed by accident. PreJudge turns the organizer's rubric into a repeatable pre-submission check you can
run after every change (and in CI with `--fail-under`).

## What one run does

```
rules.md ──► parse rubric + checklist (deterministic, weights from the organizer)
                │
project/ ──►  [1] Judge agent ── tools: submission_scan (forced first), repo_tree, read_file, search_repo
                │      output_type=Scorecard (Pydantic), one durable session per project
                ▼
              Evidence gate ── every quote is checked against the file; a score > 5 without
                │               a verifiable quote is capped at 5
                ▼
              [2] Skeptic subagent (subagent(), bounded timeout, output_type=SkepticReview)
                │      sees scorecard + scan, challenges scores; PreJudge meets it halfway
                ▼
              [3] optional Fixer agent ── create_doc tool (write scope → approval_callback asks you)
                ▼
          weighted score (computed in Python) · history delta · PREJUDGE_REPORT.md · prejudge_report.html
```

The model never computes the final number: weights come from the rules file and the total is calculated
in code, so the score is reproducible and cannot be talked up.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/ElTanque81/prejudge.git
cd prejudge
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'

# configure a real model once (OpenMinion setup wizard)
openminion setup --no-chat          # choose e.g. cortensor-portal, groq, openai, ollama
openminion doctor --check-turn      # one real test turn
```

Using **Cortensor Portal**: pick `cortensor-portal` in the wizard (credential `CORTENSOR_API_KEY`,
endpoint `https://api.cortensor.app/v1`, recommended model `oss-20b`). Portal is slower than most hosted
providers, so pass `--timeout 900`.

## Run

```bash
# judge the bundled sample project against Cortensor Hackathon #5
prejudge cortensor-hackathon-5 examples/sample-project --config ~/.openminion/agents.json

# German report, let the fixer draft missing docs (asks before every file)
prejudge cortensor-hackathon-5 . --lang de --fix

# any hackathon: save the rules page text to a file and point PreJudge at it
prejudge my-hackathon-rules.txt ~/code/my-project

# CI gate: exit code 3 when the weighted score is below 70
prejudge cortensor-hackathon-5 . --fail-under 70
```

Output goes to `PROJECT/.prejudge/` (`PREJUDGE_REPORT.md`, `prejudge_report.html`, `history.json`).
Run it again after fixing things and the report shows the delta against the previous run.

| Option | Meaning |
|---|---|
| `--lang en\|de` | report language (or `PREJUDGE_LANG`) |
| `--config`, `--agent` | OpenMinion `agents.json` and agent profile |
| `--no-skeptic` | skip the skeptic subagent (faster, fewer calls) |
| `--fix` / `--yes` | fixer agent; `--yes` auto-approves file creation |
| `--timeout` | seconds per agent turn (default 600) |
| `--fail-under N` | exit 3 below N/100 |

### Rules file format

PreJudge needs weighted lines such as `Working functionality and useful outcome: 25%` and, ideally, a
checklist after a line like `Your PR should include:` (markdown bullets or plain lines pasted from the
web page both work). See `prejudge/rules_library/cortensor-hackathon-5.md`.

## The OpenMinion SDK workflow

| Building block | Where |
|---|---|
| `Agent` + `@tool` with `min_scope="READ_ONLY"` | `prejudge/repo_tools.py` – four read tools bound to one project folder; paths cannot escape it |
| `forced_tools` | judge must call `submission_scan` first, so every score starts from the same facts |
| `output_type` (Pydantic) + `AgentOutputValidationError` | `Scorecard`, `SkepticReview`; one repair turn in the same session on invalid JSON |
| Sessions | one durable `session_id` per project + rules (`prejudge-<name>-<hash>`), reused on every run |
| Delegation: `subagent()` | skeptic reviewer with its own instructions, output type and timeout |
| Permissions + `approval_callback` | `create_doc` keeps write scope and `idempotent=False`, so the action policy asks; the CLI shows path + preview and waits for y/N |
| Diagnostics: `AgentRunResult.stats` | LLM calls, tokens and seconds per stage are printed in every report |
| Errors: `ProviderError`, `TurnTimeoutError` | clean CLI messages with next steps |

Details on what worked and what didn't: [BUILDING_BLOCKS.md](BUILDING_BLOCKS.md) and
[WHAT_WE_LEARNED.md](WHAT_WE_LEARNED.md). Bug report drafts: [docs/BUG_REPORTS.md](docs/BUG_REPORTS.md).

## Tests (offline, no API key)

```bash
pytest -q
```

`tests/test_offline.py` covers the deterministic parts and one **full run through the real OpenMinion
runtime** against `tests/mock_model.py`, a scripted OpenAI-compatible server. The mock is not a model;
it lets the tool registry, action policy, approvals, sessions, subagent and output validation run in CI.
The demo video uses a real model.

## Demo

📹 Demo video (1–5 min): **TODO – add link**

## Limits

- Scores are an estimate from one model, not a prediction of the real judging.
- Read tools see text files under 400 KB; binaries and images are listed but not read.
- The secret scan is a few regexes (OpenAI/GitHub/AWS keys, private keys, `.env`) – a hint, not an audit.

## License

Apache-2.0. Not affiliated with OpenMinion or Cortensor.

---

## Kurzanleitung auf Deutsch

PreJudge bewertet dein Hackathon-Projekt **vor der Abgabe** mit den Kriterien und Gewichten des Veranstalters.
Jede Punktzahl braucht ein Zitat aus deinem Code, das automatisch geprüft wird; ein zweiter, skeptischer
Juror-Agent korrigiert zu großzügige Wertungen. Am Ende bekommst du einen Bericht mit konkreten To-dos.

```bash
pip install -e .
openminion setup --no-chat                         # Modell einrichten (z. B. cortensor-portal)
prejudge cortensor-hackathon-5 . --lang de         # deutscher Bericht
prejudge cortensor-hackathon-5 . --lang de --fix   # fehlende Doku-Dateien entwerfen (mit Rückfrage)
```

Der Bericht liegt danach in `.prejudge/PREJUDGE_REPORT.md` und `.prejudge/prejudge_report.html`.
Für andere Hackathons: Regeltext der DoraHacks-Seite in eine Datei kopieren und statt
`cortensor-hackathon-5` den Dateipfad angeben.
