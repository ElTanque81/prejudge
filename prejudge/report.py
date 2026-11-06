"""Render a JudgeResult as Markdown and as a self-contained HTML report card (DE/EN)."""

from __future__ import annotations

import html
from datetime import datetime

from .pipeline import JudgeResult

T = {
    "en": {
        "title": "PreJudge report", "total": "Weighted score", "prev": "previous run", "crit": "Criterion",
        "weight": "Weight", "score": "Score", "evidence": "Evidence", "gaps": "Gaps", "fixes": "Fixes",
        "verified": "verified", "unverified": "NOT verified", "missing": "file does not exist", "adj": "Score adjustments", "skeptic": "Skeptic verdict",
        "checks": "Deterministic checks", "risks": "Top risks", "created": "Files created (approved)",
        "diag": "Run diagnostics", "stage": "Stage", "secs": "Seconds", "calls": "LLM calls", "tokens": "Tokens in/out",
        "yes": "yes", "no": "no", "readme": "README", "setup": "Setup instructions", "runi": "Run instructions",
        "video": "Demo video link", "license": "License", "tests": "Tests", "secrets": "Possible secrets",
        "none": "none", "session": "Session", "weights_warn": "Rubric weights do not add up to 100% — check the rules file.",
        "first": "first run",
    },
    "de": {
        "title": "PreJudge-Bericht", "total": "Gewichtete Punktzahl", "prev": "letzter Lauf", "crit": "Kriterium",
        "weight": "Gewicht", "score": "Punkte", "evidence": "Belege", "gaps": "Lücken", "fixes": "Nächste Schritte",
        "verified": "verifiziert", "unverified": "NICHT verifiziert", "missing": "Datei existiert nicht", "adj": "Punkte-Korrekturen", "skeptic": "Urteil des Skeptikers",
        "checks": "Automatische Prüfungen", "risks": "Größte Risiken", "created": "Erstellte Dateien (freigegeben)",
        "diag": "Lauf-Diagnose", "stage": "Schritt", "secs": "Sekunden", "calls": "LLM-Aufrufe", "tokens": "Tokens ein/aus",
        "yes": "ja", "no": "nein", "readme": "README", "setup": "Setup-Anleitung", "runi": "Start-Anleitung",
        "video": "Demo-Video-Link", "license": "Lizenz", "tests": "Tests", "secrets": "Mögliche Geheimnisse",
        "none": "keine", "session": "Session", "weights_warn": "Die Gewichte ergeben nicht 100 % – Regeldatei prüfen.",
        "first": "erster Lauf",
    },
}


def _checks(r: JudgeResult, t: dict) -> list[tuple[str, str, bool]]:
    s = r.scan
    yn = lambda b: t["yes"] if b else t["no"]
    secrets = ", ".join(f"{x['file']}:{x['line']} ({x['kind']})" for x in s["possible_secrets"]) or t["none"]
    return [
        (t["readme"], s["readme"] or t["no"], bool(s["readme"])),
        (t["setup"], yn(s["readme_has_setup"]), s["readme_has_setup"]),
        (t["runi"], yn(s["readme_has_run"]), s["readme_has_run"]),
        (t["video"], ", ".join(s["demo_video_links"]) or t["no"], bool(s["demo_video_links"])),
        (t["license"], s["license"] or t["no"], bool(s["license"])),
        (t["tests"], yn(s["has_tests"]), s["has_tests"]),
        (t["secrets"], secrets, not s["possible_secrets"]),
    ]


def _delta(r: JudgeResult, t: dict) -> str:
    if not r.previous:
        return t["first"]
    d = round(r.total - float(r.previous.get("total", 0)), 1)
    return f"{'+' if d >= 0 else ''}{d} vs {t['prev']} ({r.previous.get('total')})"


def to_markdown(r: JudgeResult, lang: str = "en") -> str:
    t = T.get(lang, T["en"])
    w = {c.name: c.weight for c in r.rules.criteria}
    out = [f"# {t['title']}: {r.rules.title}", "", f"**{t['total']}: {r.total}/100** — {_delta(r, t)}", ""]
    if not r.rules.weights_ok:
        out += [f"> ⚠️ {t['weights_warn']}", ""]
    out += [r.scorecard.project_summary, "", f"| {t['crit']} | {t['weight']} | {t['score']} |", "|---|---|---|"]
    for cs in r.scorecard.criteria:
        out.append(f"| {cs.criterion} | {round(w.get(cs.criterion, 0) * 100)}% | {cs.score}/10 |")
    for cs in r.scorecard.criteria:
        out += ["", f"## {cs.criterion} — {cs.score}/10"]
        flags = r.verified.get(cs.criterion, [])
        if cs.evidence:
            out.append(f"**{t['evidence']}:**")
            for e, ok in zip(cs.evidence, flags or [False] * len(cs.evidence)):
                mark = f"✅ {t['verified']}" if ok else (f"❌ {t['unverified']}" if e.path in r.existing else f"🚫 {t['missing']}")
                q = f' — "{e.quote[:140]}"' if e.quote else ""
                out.append(f"- `{e.path}`{q} ({mark})")
        if cs.gaps:
            out.append(f"**{t['gaps']}:**")
            out += [f"- {g}" for g in cs.gaps]
        if cs.fixes:
            out.append(f"**{t['fixes']}:**")
            out += [f"- [ ] {f}" for f in cs.fixes]
    if r.scorecard.top_risks:
        out += ["", f"## {t['risks']}"] + [f"- {x}" for x in r.scorecard.top_risks]
    if r.adjustments:
        out += ["", f"## {t['adj']}"] + [f"- {a.criterion}: {a.before} → {a.after} ({a.reason})" for a in r.adjustments]
    if r.skeptic:
        out += ["", f"**{t['skeptic']}:** {r.skeptic.verdict}"]
    out += ["", f"## {t['checks']}"] + [f"- {'✅' if ok else '⚠️'} {k}: {v}" for k, v, ok in _checks(r, t)]
    if r.created_files:
        out += ["", f"## {t['created']}"] + [f"- `{f}`" for f in r.created_files]
    out += ["", f"## {t['diag']}", f"| {t['stage']} | {t['secs']} | {t['calls']} | {t['tokens']} |", "|---|---|---|---|"]
    for s in r.stages:
        out.append(f"| {s.name} | {s.seconds} | {s.stats.get('llm_calls', '?')} | {s.stats.get('input_tokens', '?')}/{s.stats.get('output_tokens', '?')} |")
    out += ["", f"{t['session']}: `{r.session_id}` · {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    return "\n".join(out) + "\n"


def to_html(r: JudgeResult, lang: str = "en") -> str:
    t = T.get(lang, T["en"])
    e = html.escape
    w = {c.name: c.weight for c in r.rules.criteria}
    color = "#1f9d55" if r.total >= 75 else "#d69e2e" if r.total >= 50 else "#e53e3e"
    rows = []
    for cs in r.scorecard.criteria:
        flags = r.verified.get(cs.criterion, [])
        ev = "".join(
            f"<li><code>{e(x.path)}</code> {('— “' + e(x.quote[:140]) + '”') if x.quote else ''} "
            f"<span class='{'ok' if ok else 'bad'}'>{t['verified'] if ok else (t['unverified'] if x.path in r.existing else t['missing'])}</span></li>"
            for x, ok in zip(cs.evidence, flags or [False] * len(cs.evidence))
        )
        gaps = "".join(f"<li>{e(g)}</li>" for g in cs.gaps)
        fixes = "".join(f"<li><label><input type='checkbox'> {e(f)}</label></li>" for f in cs.fixes)
        rows.append(
            f"<section class='crit'><div class='head'><h3>{e(cs.criterion)}</h3>"
            f"<span class='w'>{round(w.get(cs.criterion, 0) * 100)}%</span>"
            f"<div class='bar'><i style='width:{cs.score * 10}%'></i></div><b>{cs.score}/10</b></div>"
            + (f"<h4>{t['evidence']}</h4><ul>{ev}</ul>" if ev else "")
            + (f"<h4>{t['gaps']}</h4><ul>{gaps}</ul>" if gaps else "")
            + (f"<h4>{t['fixes']}</h4><ul class='fix'>{fixes}</ul>" if fixes else "")
            + "</section>"
        )
    checks = "".join(f"<li class='{'ok' if ok else 'bad'}'>{'✓' if ok else '!'} {e(k)}: {e(str(v))}</li>" for k, v, ok in _checks(r, t))
    adj = "".join(f"<li>{e(a.criterion)}: {a.before} → {a.after} <small>{e(a.reason)}</small></li>" for a in r.adjustments)
    risks = "".join(f"<li>{e(x)}</li>" for x in r.scorecard.top_risks)
    diag = "".join(
        f"<tr><td>{e(s.name)}</td><td>{s.seconds}</td><td>{s.stats.get('llm_calls', '?')}</td>"
        f"<td>{s.stats.get('input_tokens', '?')}/{s.stats.get('output_tokens', '?')}</td></tr>"
        for s in r.stages
    )
    return f"""<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(t['title'])} · {e(r.rules.title)}</title><style>
:root{{--bg:#0f1115;--card:#171a21;--fg:#e8e8ea;--mut:#9aa0aa;--acc:{color}}}
@media (prefers-color-scheme:light){{:root{{--bg:#f6f6f4;--card:#fff;--fg:#16181d;--mut:#5d636e}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:32px 20px 60px}}
.hero{{display:flex;gap:28px;align-items:center;flex-wrap:wrap}}
.dial{{width:150px;height:150px;border-radius:50%;display:grid;place-items:center;
background:conic-gradient(var(--acc) {r.total * 3.6}deg,#8882 0);flex:none}}
.dial div{{width:118px;height:118px;border-radius:50%;background:var(--bg);display:grid;place-items:center;text-align:center}}
.dial b{{font-size:34px;line-height:1}} .dial small{{color:var(--mut)}}
h1{{font-size:22px;margin:0 0 6px}} .mut{{color:var(--mut)}}
.crit,.box{{background:var(--card);border-radius:12px;padding:16px 18px;margin:14px 0;border:1px solid #8882}}
.head{{display:flex;align-items:center;gap:12px}} .head h3{{margin:0;font-size:16px;flex:1}}
.w{{color:var(--mut);font-size:13px}} .bar{{width:140px;height:8px;background:#8883;border-radius:9px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--acc)}} h4{{margin:12px 0 4px;font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}}
ul{{margin:0;padding-left:20px}} .fix{{list-style:none;padding-left:0}} code{{font-size:13px}}
.ok{{color:#1f9d55}} .bad{{color:#e53e3e}} li.ok,li.bad{{list-style:none}} table{{border-collapse:collapse;width:100%}}
td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #8882;font-size:13px}}
</style></head><body><main>
<div class="hero"><div class="dial"><div><span><b>{r.total}</b><br><small>/100</small></span></div></div>
<div><h1>{e(t['title'])}: {e(r.rules.title)}</h1><div class="mut">{e(_delta(r, t))}</div>
<p>{e(r.scorecard.project_summary)}</p></div></div>
{''.join(rows)}
{f"<div class='box'><h4>{t['risks']}</h4><ul>{risks}</ul></div>" if risks else ''}
{f"<div class='box'><h4>{t['adj']}</h4><ul>{adj}</ul>" + (f"<p class='mut'>{t['skeptic']}: {e(r.skeptic.verdict)}</p>" if r.skeptic else '') + "</div>" if adj or r.skeptic else ''}
<div class="box"><h4>{t['checks']}</h4><ul>{checks}</ul></div>
<div class="box"><h4>{t['diag']}</h4><table><tr><th>{t['stage']}</th><th>{t['secs']}</th><th>{t['calls']}</th><th>{t['tokens']}</th></tr>{diag}</table>
<p class="mut">{t['session']}: <code>{e(r.session_id)}</code></p></div>
</main></body></html>"""
