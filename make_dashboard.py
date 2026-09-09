"""
make_dashboard.py — build dashboard.html from every run on disk.

Scans runs/*/pipeline_result.json and baselines/*/pipeline_result.json and
renders a single self-contained page.

Design direction: "ledger, forensic, assured". The reference is a printed
financial register rather than a SaaS dashboard — ruled columns, tabular
figures, 1px rules, warm paper ground. Data is the ornament; there are no
floating cards.

    python make_dashboard.py
    python make_dashboard.py --out dashboard.html --labels run_labels.json

run_labels.json is optional: {"job_20260823_014521": "Web iPhone SE"} names
runs the data cannot distinguish by itself (a viewport run looks like an
ordinary web run in the result file).
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# ── design tokens ─────────────────────────────────────────────────────────────
GROUND    = "#F2EFE8"   # papier chaud
SURFACE   = "#FBFAF7"   # papier plus clair, pour la matrice
INK       = "#14181C"   # off-black chaud
INK_SOFT  = "#5E6670"
SIGNATURE = "#A67C2E"   # champagne — filets actifs, chiffres clés
PASS_C    = "#2E7D5B"
FAIL_C    = "#B03A2E"
SKIP_C    = "#8A8F98"
RULE      = "#D8D2C6"


# ── chargement ────────────────────────────────────────────────────────────────

def job_id_of(p: Path) -> str:
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("job_id", p.parent.name)
    except (json.JSONDecodeError, OSError):
        return p.parent.name


def find_runs(root: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    for base in ("runs", "baselines"):
        for p in sorted((root / base).glob("*/pipeline_result.json")):
            seen.setdefault(job_id_of(p), p)
    return [seen[k] for k in sorted(seen)]


def load_run(p: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return d if "results" in d else None


def channel_of(run: dict[str, Any]) -> str:
    return "api" if any(r.get("channel") == "api" for r in run["results"]) else "web"


def judge_mode(run: dict[str, Any]) -> str:
    sources = {(r.get("judgment") or {}).get("source") for r in run["results"]}
    sources.discard(None)
    graded = sources - {"veto", "passthrough"}
    if graded:
        return sorted(graded)[0]
    return "passthrough" if "passthrough" in sources else "inconnu"


def when_of(job_id: str) -> str:
    m = re.search(r"(\d{8})_(\d{6})", job_id)
    if not m:
        return job_id
    d, t = m.groups()
    return f"{d[6:8]}.{d[4:6]} {t[:2]}h{t[2:4]}"


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


# ── page ──────────────────────────────────────────────────────────────────────

def build(root: Path, labels: dict[str, str]) -> str:
    runs: list[dict[str, Any]] = []
    for p in find_runs(root):
        d = load_run(p)
        if d and d.get("results"):
            d["_channel"] = channel_of(d)
            d["_judge"] = judge_mode(d)
            d["_label"] = labels.get(d.get("job_id", ""), "")
            runs.append(d)
    if not runs:
        raise SystemExit("erreur : aucun pipeline_result.json sous runs/ ou baselines/")

    runs.sort(key=lambda d: d.get("job_id", ""))
    latest = runs[-1]

    def skey(s: str) -> tuple:
        m = re.match(r"S(\d+)", s)
        return (0, int(m.group(1))) if m else (1, s)

    scenarios = sorted({r.get("scenario_id", "?") for d in runs for r in d["results"]},
                       key=skey)

    per_run: list[dict[str, dict]] = []
    for d in runs:
        idx: dict[str, dict] = {}
        for r in d["results"]:
            j = r.get("judgment") or {}
            idx[r.get("scenario_id", "?")] = {
                "v": r.get("verdict", "?"),
                "score": j.get("score"),
                "reason": (j.get("reason") or "")[:260],
                "intent": r.get("intent", ""),
            }
        per_run.append(idx)

    labels_full = [d["_label"] or f'{d["_channel"]} — {when_of(d.get("job_id",""))}'
                   for d in runs]

    chart = {
        "ids": [d.get("job_id", "?") for d in runs],
        "label": labels_full,
        "channel": [d["_channel"] for d in runs],
        "judge": [d["_judge"] for d in runs],
        "rate": [d.get("pass_rate", 0) for d in runs],
        "total": [d.get("total", len(d["results"])) for d in runs],
        "scores": [[(r.get("judgment") or {}).get("score") for r in d["results"]
                    if (r.get("judgment") or {}).get("score") is not None]
                   for d in runs],
    }

    # données de la « ligne de vie » : verdicts d'un scénario au fil des campagnes
    lifeline = {
        sid: {
            "intent": next((i[sid]["intent"] for i in reversed(per_run) if sid in i), ""),
            "v": [(i[sid]["v"] if sid in i else None) for i in per_run],
            "s": [(i[sid]["score"] if sid in i else None) for i in per_run],
            "why": next((i[sid]["reason"] for i in reversed(per_run)
                         if sid in i and i[sid]["v"] == "FAIL"), ""),
        } for sid in scenarios
    }

    # ── agrégats projet ──
    n_camp = len(runs)
    n_exec = sum(d.get("total", len(d["results"])) for d in runs)
    channels = sorted({d["_channel"] for d in runs})

    by_channel: dict[str, dict[str, Any]] = {}
    for ch in channels:
        rs = [d for d in runs if d["_channel"] == ch]
        judged = [d for d in rs if d["_judge"] not in ("passthrough", "inconnu")]
        ref = judged[-1] if judged else rs[-1]
        by_channel[ch] = {
            "campagnes": len(rs),
            "scenarios": ref.get("total", 0),
            "taux": ref.get("pass_rate", 0),
            "label": ref["_label"] or when_of(ref.get("job_id", "")),
        }

    # fragilité : combien de campagnes chaque scénario échoue-t-il ?
    frag = []
    for sid in scenarios:
        seen = [i[sid] for i in per_run if sid in i]
        f = sum(1 for e in seen if e["v"] == "FAIL")
        p = sum(1 for e in seen if e["v"] == "PASS")
        if f:
            frag.append({
                "sid": sid,
                "intent": next((e["intent"] for e in reversed(seen) if e["intent"]), ""),
                "fail": f, "pass": p, "n": len(seen),
                "instable": f > 0 and p > 0,
                "why": next((e["reason"] for e in reversed(seen)
                             if e["v"] == "FAIL" and e["reason"]), ""),
            })
    frag.sort(key=lambda x: (-x["fail"], x["sid"]))
    n_instables = sum(1 for x in frag if x["instable"])
    n_toujours = sum(1 for x in frag if not x["instable"])

    # ── matrice ──
    head = "".join(
        f'<th><span class="ch">{d["_channel"]}</span>'
        f'<span class="hl">{esc(d["_label"] or when_of(d.get("job_id","?")))}</span></th>'
        for d in runs)

    rows = []
    for sid in scenarios:
        cells = []
        for idx in per_run:
            e = idx.get(sid)
            if not e:
                cells.append('<td class="c absent"></td>')
                continue
            cls = {"PASS": "pass", "FAIL": "fail", "SKIPPED": "skip"}.get(e["v"], "skip")
            val = "" if e["score"] is None else f'{e["score"]}'
            cells.append(f'<td class="c {cls}">{val}</td>')
        intent = lifeline[sid]["intent"]
        rows.append(f'<tr data-sid="{esc(sid)}"><th class="sid">{esc(sid)}</th>'
                    f'<td class="intent">{esc(intent)}</td>{"".join(cells)}</tr>')

    matrix = (f'<table class="matrix"><thead><tr><th class="sid">scén.</th>'
              f'<td class="intent"></td>{head}</tr></thead>'
              f'<tbody>{"".join(rows)}</tbody></table>')

    # ── scénarios en échec, toutes campagnes confondues ──
    fail_rows = "".join(
        f'<tr><td class="sid">{esc(x["sid"])}</td>'
        f'<td class="mono">{esc(x["intent"])}</td>'
        f'<td class="num">{x["fail"]}/{x["n"]}</td>'
        f'<td class="num">{"instable" if x["instable"] else "constant"}</td>'
        f'<td>{esc(x["why"])}</td></tr>'
        for x in frag) or '<tr><td colspan="5">Aucun échec sur l\'ensemble des campagnes.</td></tr>'

    # ── synthèse par canal ──
    chan_rows = "".join(
        f'<tr><td class="sid">{esc(ch)}</td>'
        f'<td class="num">{v["campagnes"]}</td>'
        f'<td class="num">{v["scenarios"]}</td>'
        f'<td class="num">{v["taux"]} %</td>'
        f'<td class="mono">{esc(v["label"])}</td></tr>'
        for ch, v in by_channel.items())

    warn = ""
    if any(d["_judge"] == "passthrough" for d in runs):
        warn = ('<p class="warn"><strong>Avertissement.</strong> Une campagne au moins '
                'a été exécutée sans le juge sémantique. Son taux ne mesure que le bon '
                'déroulement technique, pas la qualité des réponses ; il ne doit pas être '
                'comparé aux campagnes jugées.</p>')

    rate = latest.get("pass_rate", "—")
    generated = datetime.now().strftime("%d.%m.%Y")

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaRuche QA — registre des campagnes</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {{
    --ground:{GROUND}; --surface:{SURFACE}; --ink:{INK}; --soft:{INK_SOFT};
    --sig:{SIGNATURE}; --pass:{PASS_C}; --fail:{FAIL_C}; --skip:{SKIP_C};
    --rule:{RULE};
  }}
  *{{box-sizing:border-box}}
  html,body{{margin:0}}
  body{{
    background:var(--ground); color:var(--ink);
    font:15px/1.6 "IBM Plex Sans",sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  body::before{{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:1; opacity:.025;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)'/%3E%3C/svg%3E");
  }}
  .wrap{{max-width:1240px;margin:0 auto;padding:0 34px}}

  /* ── hero : grille asymétrique ── */
  header{{padding:64px 0 0}}
  .hero{{display:grid;grid-template-columns:repeat(12,1fr);gap:22px;align-items:end}}
  .hero h1{{
    grid-column:1/9; margin:0;
    font-family:Fraunces,serif; font-weight:600;
    font-size:clamp(40px,6.4vw,78px); line-height:.96; letter-spacing:-.022em;
  }}
  .hero h1 .rate{{
    font-family:"IBM Plex Mono",monospace; font-weight:500;
    font-size:.42em; color:var(--sig); letter-spacing:-.01em;
    vertical-align:.42em; margin-left:.3em; white-space:nowrap;
  }}
  .meta{{grid-column:10/13; font-family:"IBM Plex Mono",monospace; font-size:12px;
        line-height:1.75; color:var(--soft); padding-bottom:6px}}
  .meta b{{color:var(--ink); font-weight:500}}
  .meta .row{{display:flex; justify-content:space-between; gap:12px;
              border-bottom:1px solid var(--rule); padding:3px 0}}
  .meta .row:last-child{{border-bottom:0}}
  .sig-rule{{height:1px;background:var(--sig);margin:30px 0 0}}
  .lede{{max-width:62ch;margin:22px 0 0;color:var(--soft)}}
  .action{{
    display:inline-block; margin:18px 0 0; font-weight:500; color:var(--ink);
    text-decoration:none; border-bottom:1.5px solid var(--sig); padding-bottom:2px;
    transition:letter-spacing .18s cubic-bezier(.2,.7,.3,1);
  }}
  .action:hover{{letter-spacing:.02em}}
  .warn{{
    margin:22px 0 0; padding:12px 0 12px 16px; max-width:78ch;
    border-left:2px solid var(--fail); color:#7A2B22; font-size:14px;
  }}

  /* ── sections ── */
  section{{margin:72px 0 0}}
  .shead{{display:flex;align-items:baseline;gap:16px;
          border-bottom:1px solid var(--ink);padding-bottom:8px}}
  .shead h2{{font-family:Fraunces,serif;font-weight:600;font-size:26px;margin:0;
             letter-spacing:-.01em}}
  .shead .n{{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--sig)}}
  .hint{{color:var(--soft);margin:14px 0 20px;max-width:74ch;font-size:14.5px}}

  /* ── matrice, pleine largeur ── */
  .bleed{{background:var(--surface);border-top:1px solid var(--rule);
          border-bottom:1px solid var(--rule)}}
  .mwrap{{overflow-x:auto}}
  table.matrix{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
  .matrix thead th{{
    font:500 11.5px/1.35 "IBM Plex Mono",monospace; color:var(--soft);
    padding:10px 8px; text-align:center; vertical-align:bottom;
    border-bottom:1px solid var(--ink);
  }}
  .matrix thead .ch{{display:block;color:var(--sig);font-size:10px;letter-spacing:.08em;
                     text-transform:uppercase}}
  .matrix thead .hl{{display:block;color:var(--ink);font-weight:500}}
  .matrix tbody tr{{transition:background .14s ease}}
  .matrix tbody tr:hover{{background:#fff}}
  .matrix tbody tr.on td,.matrix tbody tr.on th{{
    border-top:1px solid var(--sig);border-bottom:1px solid var(--sig)}}
  .matrix th.sid{{
    font:500 12.5px "IBM Plex Mono",monospace; color:var(--sig);
    text-align:left; padding:5px 10px; white-space:nowrap;
    border-bottom:1px solid var(--rule);
  }}
  .matrix td.intent{{
    font:400 12.5px "IBM Plex Mono",monospace; color:var(--soft);
    padding:5px 10px; max-width:190px; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; border-bottom:1px solid var(--rule);
  }}
  .matrix td.c{{
    text-align:center; font:500 12px "IBM Plex Mono",monospace;
    min-width:78px; padding:5px 6px; border-bottom:1px solid var(--rule);
    border-left:1px solid var(--rule);
  }}
  td.c.pass{{color:var(--pass);background:rgba(46,125,91,.09)}}
  td.c.fail{{color:var(--fail);background:rgba(176,58,46,.10)}}
  td.c.skip{{color:var(--skip);background:rgba(138,143,152,.10)}}
  td.c.absent{{background:repeating-linear-gradient(45deg,transparent,transparent 5px,
               rgba(0,0,0,.035) 5px,rgba(0,0,0,.035) 6px)}}
  .legend{{font:400 12.5px "IBM Plex Mono",monospace;color:var(--soft);
           padding:12px 0 0;display:flex;gap:22px;flex-wrap:wrap}}
  .legend i{{font-style:normal}}
  .legend i::before{{content:"";display:inline-block;width:22px;height:2px;
                     vertical-align:.24em;margin-right:7px}}
  .lg-p::before{{background:var(--pass)}} .lg-f::before{{background:var(--fail)}}
  .lg-s::before{{background:var(--skip)}} .lg-a::before{{background:var(--rule)}}

  /* ── ligne de vie ── */
  #life{{
    position:fixed; right:26px; bottom:26px; width:330px; z-index:5;
    background:var(--surface); border:1px solid var(--rule);
    border-top:2px solid var(--sig); padding:16px 18px;
    font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--soft);
    opacity:0; transform:translateY(8px); pointer-events:none;
    transition:opacity .2s ease, transform .28s cubic-bezier(.2,.7,.3,1);
    box-shadow:0 12px 30px rgba(20,24,28,.10);
  }}
  #life.on{{opacity:1;transform:translateY(0)}}
  #life .id{{font-size:16px;color:var(--ink);font-weight:500}}
  #life .in{{color:var(--sig);margin:0 0 10px}}
  #life .sp svg{{display:block;margin:4px 0 10px}}
  #life .why{{color:var(--soft);line-height:1.5;font-size:11.5px;
              border-top:1px solid var(--rule);padding-top:8px}}

  /* ── graphiques ── */
  .charts{{display:grid;grid-template-columns:7fr 5fr;
           border-top:1px solid var(--rule)}}
  .plot{{padding:8px 0}}
  .charts .plot+.plot{{border-left:1px solid var(--rule);padding-left:14px}}
  @media(max-width:900px){{
    .charts{{grid-template-columns:1fr}}
    .charts .plot+.plot{{border-left:0;padding-left:0;border-top:1px solid var(--rule)}}
    .hero h1{{grid-column:1/13}} .meta{{grid-column:1/13}}
    #life{{display:none}}
  }}

  /* ── table des échecs ── */
  table.fails{{border-collapse:collapse;width:100%;font-size:13.5px}}
  .fails th{{text-align:left;font:500 11px "IBM Plex Mono",monospace;
             letter-spacing:.08em;text-transform:uppercase;color:var(--soft);
             padding:8px 12px 8px 0;border-bottom:1px solid var(--ink)}}
  .fails td{{padding:9px 12px 9px 0;border-bottom:1px solid var(--rule);
             vertical-align:top}}
  .fails td.sid{{font:500 12.5px "IBM Plex Mono",monospace;color:var(--sig);
                 white-space:nowrap}}
  .fails td.mono,.fails td.num{{font-family:"IBM Plex Mono",monospace;
                                font-size:12.5px;white-space:nowrap}}
  footer{{margin:72px 0 0;border-top:1px solid var(--ink);padding:16px 0 60px;
          color:var(--soft);font-size:13px;max-width:82ch}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="hero">
      <h1>LaRuche<br>QA Pipeline</h1>
      <div class="meta">
        <div class="row"><span>campagnes</span><b>{n_camp}</b></div>
        <div class="row"><span>exécutions</span><b>{n_exec}</b></div>
        <div class="row"><span>scénarios</span><b>{len(scenarios)}</b></div>
        <div class="row"><span>canaux</span><b>{esc(" · ".join(channels))}</b></div>
        <div class="row"><span>éditée le</span><b>{generated}</b></div>
      </div>
    </div>
    <div class="sig-rule"></div>
    <p class="lede">Registre de <strong>l'ensemble des campagnes</strong>
    exécutées contre l'agent <strong>LaRuche</strong> au cours du projet. Chaque
    campagne interroge le produit par ses canaux réels, note ses réponses avec un
    juge local et conserve les preuves. La lecture utile n'est pas le taux d'une
    campagne isolée mais la stabilité d'un scénario d'une campagne à l'autre.</p>
    <a class="action" href="regression.md">Ouvrir le rapport de régression →</a>
    {warn}
  </header>

  <section>
    <div class="shead"><span class="n">01</span><h2>Synthèse par canal</h2></div>
    <p class="hint">Taux de la campagne jugée la plus récente pour chaque canal.
    Deux chaînes d'exécution distinctes convergeant vers un taux voisin est un
    indice que les deux mesurent la même propriété.</p>
    <table class="fails">
      <thead><tr><th>Canal</th><th>Campagnes</th><th>Scénarios</th>
      <th>Taux</th><th>Campagne de référence</th></tr></thead>
      <tbody>{chan_rows}</tbody>
    </table>
  </section>

  <section>
    <div class="shead"><span class="n">02</span><h2>Matrice des verdicts</h2></div>
    <p class="hint">Une ligne par scénario, une colonne par campagne ; la valeur
    est la note du juge sur 5. Survoler une ligne ouvre sa ligne de vie : lire
    une ligne dit si un scénario est stable, lire une colonne dit ce qu'une
    campagne a trouvé.</p>
  </section>
</div>

<div class="bleed">
  <div class="wrap"><div class="mwrap">{matrix}</div></div>
</div>

<div class="wrap">
  <p class="legend">
    <i class="lg-p">réussite</i><i class="lg-f">échec</i>
    <i class="lg-s">ignoré</i><i class="lg-a">absent de la campagne</i>
  </p>

  <section>
    <div class="shead"><span class="n">03</span><h2>Taux et distributions</h2></div>
    <p class="hint">Un juge qui ne rend que des notes maximales n'évalue rien :
    la dispersion des notes est le contrôle de vraisemblance le plus direct.</p>
    <div class="charts">
      <div class="plot" id="rate" style="height:330px"></div>
      <div class="plot" id="scores" style="height:330px"></div>
    </div>
  </section>

  <section>
    <div class="shead"><span class="n">04</span><h2>Scénarios en échec</h2></div>
    <p class="hint">Tous les scénarios ayant échoué au moins une fois, toutes
    campagnes confondues, classés par fréquence. Un scénario
    <strong>constant</strong> échoue à chaque campagne : c'est une défaillance
    réelle du produit. Un scénario <strong>instable</strong> alterne réussite et
    échec : soit le produit varie, soit le juge hésite — dans les deux cas, le
    verdict isolé ne suffit pas.</p>
    <table class="fails">
      <thead><tr><th>Scén.</th><th>Intention</th><th>Échecs</th>
      <th>Stabilité</th><th>Dernier motif</th></tr></thead>
      <tbody>{fail_rows}</tbody>
    </table>
    <p class="legend" style="padding-top:16px">
      <i class="lg-f">{n_toujours} scénario(s) en échec constant</i>
      <i class="lg-a">{n_instables} scénario(s) instable(s)</i>
    </p>
  </section>

  <footer>Pipeline construit avec LangGraph, Selenium et Ollama, avec détection
  de régression et code de sortie exploitable en intégration continue. Les
  preuves de chaque scénario — captures d'écran et échanges — sont conservées
  dans runs/&lt;job&gt;/.</footer>
</div>

<div id="life">
  <div class="id"></div><div class="in"></div>
  <div class="sp"></div><div class="why"></div>
</div>

<script>
const D = {json.dumps(chart, ensure_ascii=False)};
const L = {json.dumps(lifeline, ensure_ascii=False)};
const SIG="{SIGNATURE}", INK="{INK}", SOFT="{INK_SOFT}",
      PASS="{PASS_C}", FAIL="{FAIL_C}", RULE="{RULE}";

/* ── ligne de vie : frise des verdicts au survol d'une ligne ── */
const box = document.getElementById("life");

function sparkline(v, s) {{
  const w = 294, h = 44, n = v.length, step = n > 1 ? w / (n - 1) : 0;
  const pts = [];
  let dots = "";
  v.forEach(function (val, i) {{
    const x = n > 1 ? i * step : w / 2;
    const y = (val == null) ? h / 2 : (val === "PASS" ? 11 : h - 11);
    pts.push(x + "," + y);
    const c = (val == null) ? RULE : (val === "PASS" ? PASS : FAIL);
    dots += '<circle cx="' + x + '" cy="' + y + '" r="3.5" fill="' + c + '"/>';
    if (s[i] != null) {{
      const ty = y + (val === "PASS" ? -7 : 14);
      dots += '<text x="' + x + '" y="' + ty + '" text-anchor="middle" '
            + 'font-size="9" font-family="IBM Plex Mono" fill="' + SOFT + '">'
            + s[i] + '</text>';
    }}
  }});
  return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">'
       + '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + RULE
       + '" stroke-width="1"/>' + dots + '</svg>';
}}

document.querySelectorAll(".matrix tbody tr").forEach(function (tr) {{
  tr.addEventListener("mouseenter", function () {{
    document.querySelectorAll(".matrix tbody tr.on")
            .forEach(function (o) {{ o.classList.remove("on"); }});
    tr.classList.add("on");
    const d = L[tr.dataset.sid];
    if (!d) return;
    box.querySelector(".id").textContent = tr.dataset.sid;
    box.querySelector(".in").textContent = d.intent || "";
    box.querySelector(".sp").innerHTML = sparkline(d.v, d.s);
    box.querySelector(".why").textContent = d.why || "Aucun échec enregistré.";
    box.classList.add("on");
  }});
}});

const tbody = document.querySelector(".matrix tbody");
if (tbody) {{
  tbody.addEventListener("mouseleave", function () {{
    box.classList.remove("on");
    document.querySelectorAll(".matrix tbody tr.on")
            .forEach(function (o) {{ o.classList.remove("on"); }});
  }});
}}

/* ── graphiques, sur fond papier ── */
const ax = {{gridcolor: RULE, zerolinecolor: RULE, linecolor: RULE,
            tickfont: {{family: "IBM Plex Mono", size: 10, color: SOFT}}}};
const lay = function (t, x) {{
  return Object.assign({{
    title: {{text: t, font: {{family: "Fraunces, serif", size: 15, color: INK}},
             x: 0, xanchor: "left"}},
    font: {{family: "IBM Plex Sans", color: INK, size: 11}},
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    margin: {{l: 44, r: 12, t: 42, b: 78}},
    xaxis: Object.assign({{}}, ax), yaxis: Object.assign({{}}, ax)
  }}, x || {{}});
}};
const cfg = {{displayModeBar: false, responsive: true}};
const col = function (c) {{ return c === "api" ? "#4B6B57" : "#3C4A5E"; }};

Plotly.newPlot("rate", [{{
  x: D.label, y: D.rate, type: "bar",
  marker: {{color: D.channel.map(col)}},
  text: D.rate.map(function (v) {{ return v + " %"; }}), textposition: "outside",
  textfont: {{family: "IBM Plex Mono", size: 10, color: INK}},
  customdata: D.ids.map(function (id, i) {{ return [id, D.judge[i], D.total[i]]; }}),
  hovertemplate: "%{{customdata[0]}}<br>%{{y}} %% — %{{customdata[2]}} scénarios"
               + "<br>juge : %{{customdata[1]}}<extra></extra>"
}}], lay("Taux de réussite par campagne",
        {{yaxis: Object.assign({{range: [0, 108], ticksuffix: " %"}}, ax)}}), cfg);

Plotly.newPlot("scores",
  D.scores.map(function (s, i) {{
    return {{y: s, type: "box", name: D.label[i], boxpoints: "all",
            jitter: 0.35, pointpos: 0,
            marker: {{color: col(D.channel[i]), size: 3.5}},
            line: {{color: col(D.channel[i]), width: 1.2}}}};
  }}),
  lay("Notes du juge (1 à 5)",
      {{showlegend: false, yaxis: Object.assign({{range: [0.5, 5.5]}}, ax)}}), cfg);
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Génère dashboard.html depuis runs/.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--labels", default="run_labels.json")
    args = ap.parse_args()

    root = Path(args.root)
    labels: dict[str, str] = {}
    lp = root / args.labels
    if lp.exists():
        labels = json.loads(lp.read_text(encoding="utf-8"))

    out = root / args.out
    out.write_text(build(root, labels), encoding="utf-8")
    print(f"Dashboard écrit : {out}")
    print("Ouvrir dans un navigateur (connexion requise au premier chargement "
          "pour Plotly et les polices).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
