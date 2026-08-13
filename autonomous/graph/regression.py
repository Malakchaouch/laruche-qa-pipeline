"""
Regression detection — compare two PipelineResult runs.

This is the project's stated success criterion: automatically detect when a new
version of the chatbot is WORSE than a previous one, without a human reading any
answers.

Follows the categories established in the v1 implementation
(swarm_qa/regression.py) and adds two that matter for a per-scenario pipeline:

  flip          PASS -> FAIL          the clearest regression
  score_drop    judge score fell by >= score_drop_threshold (still passing)
  latency_spike execution got slower by >= latency_spike_pct
  new_failure   scenario only present/failing in the candidate
  fix           FAIL -> PASS          an improvement (reported, never a regression)
  stable_fail   FAIL in both runs     pre-existing, unchanged (never a regression)

`stable_fail` exists so a reader can tell "6 failures, 1 regression" apart from
"the report lost 5 failures". Those scenarios were already broken before the
change, so they are context, not news.

Comparison is keyed on scenario_id, so runs with different corpora still line up
on the scenarios they share; anything unmatched is reported separately rather
than silently dropped.

Only `flip`, `score_drop`, `latency_spike` and `new_failure` count as
regressions. `has_regressions` is what CI would gate on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SCORE_DROP = 0.5      # judge points (1-5 scale)
DEFAULT_LATENCY_PCT = 50.0    # percent slower

# Kinds that are reported but must never fail a build.
NON_REGRESSION_KINDS = {"fix", "stable_fail"}


@dataclass
class ScenarioDiff:
    scenario_id: str
    intent: str = ""
    baseline_verdict: str = ""
    candidate_verdict: str = ""
    baseline_score: float | None = None
    candidate_score: float | None = None
    score_delta: float | None = None
    baseline_ms: float = 0.0
    candidate_ms: float = 0.0
    latency_delta_ms: float = 0.0
    latency_delta_pct: float = 0.0
    kinds: list[str] = field(default_factory=list)   # flip / score_drop / ...
    reason: str = ""
    judge_source: str = ""                            # qwen / veto / passthrough

    @property
    def is_regression(self) -> bool:
        return any(k not in NON_REGRESSION_KINDS for k in self.kinds)


@dataclass
class RegressionReport:
    baseline_id: str
    candidate_id: str
    baseline_pass_rate: float = 0.0
    candidate_pass_rate: float = 0.0
    compared: int = 0
    baseline_judge: str = "unknown"       # qwen / passthrough / mixed
    candidate_judge: str = "unknown"
    generated_at: str = ""
    only_in_baseline: list[str] = field(default_factory=list)
    only_in_candidate: list[str] = field(default_factory=list)
    diffs: list[ScenarioDiff] = field(default_factory=list)

    def _of_kind(self, kind: str) -> list[ScenarioDiff]:
        return [d for d in self.diffs if kind in d.kinds]

    @property
    def flips(self) -> list[ScenarioDiff]:
        return self._of_kind("flip")

    @property
    def score_drops(self) -> list[ScenarioDiff]:
        return [d for d in self._of_kind("score_drop") if "flip" not in d.kinds]

    @property
    def latency_spikes(self) -> list[ScenarioDiff]:
        return self._of_kind("latency_spike")

    @property
    def new_failures(self) -> list[ScenarioDiff]:
        return self._of_kind("new_failure")

    @property
    def fixes(self) -> list[ScenarioDiff]:
        return self._of_kind("fix")

    @property
    def stable_failures(self) -> list[ScenarioDiff]:
        return self._of_kind("stable_fail")

    @property
    def regressions(self) -> list[ScenarioDiff]:
        return [d for d in self.diffs if d.is_regression]

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    @property
    def pass_rate_delta(self) -> float:
        return round(self.candidate_pass_rate - self.baseline_pass_rate, 1)

    @property
    def judge_mismatch(self) -> bool:
        """True when the two runs were not judged the same way.

        Comparing a judged run against a passthrough run is meaningless: the
        passthrough run only checked that execution succeeded, so every quality
        difference is invisible. Worth shouting about rather than hiding.
        """
        return (
            self.baseline_judge != self.candidate_judge
            and "unknown" not in (self.baseline_judge, self.candidate_judge)
        )

    @property
    def verdict_sentence(self) -> str:
        """One line a human reads last and quotes in a report."""
        n = len(self.regressions)
        if n == 0:
            return "No regression detected — the agent did not get worse."
        flips = len(self.flips)
        parts = []
        if flips:
            parts.append(f"{flips} scenario(s) went from PASS to FAIL")
        drops = len(self.score_drops)
        if drops:
            parts.append(f"{drops} score drop(s) without a flip")
        spikes = len(self.latency_spikes)
        if spikes:
            parts.append(f"{spikes} latency spike(s)")
        news = len(self.new_failures)
        if news:
            parts.append(f"{news} new failing scenario(s)")
        return f"{n} regression(s) detected — " + "; ".join(parts) + "."


# ── loading ───────────────────────────────────────────────────────────────────


def load_result(path: str | Path) -> dict[str, Any]:
    """Load a pipeline_result.json produced by graph/run.py."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _index(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r.get("scenario_id", "?"): r for r in result.get("results", [])}


def _score_of(r: dict[str, Any]) -> float | None:
    """Numeric judge score if the SLM judged this scenario, else None."""
    j = r.get("judgment") or {}
    s = j.get("score")
    return float(s) if isinstance(s, (int, float)) else None


def _source_of(r: dict[str, Any]) -> str:
    """Which path produced the verdict: qwen / veto / passthrough."""
    j = r.get("judgment") or {}
    return str(j.get("source") or j.get("by") or "")


def _judge_mode(result: dict[str, Any]) -> str:
    """Summarise how a whole run was judged.

    A run where the SLM never scored anything is 'passthrough': it only proved
    the browser steps completed, not that the answers were any good. Vetoes are
    ignored here because they fire in both modes.
    """
    sources = {_source_of(r) for r in result.get("results", [])}
    sources.discard("")
    graded = sources - {"veto", "passthrough"}
    if graded and "passthrough" in sources:
        return "mixed"
    if graded:
        return sorted(graded)[0]          # e.g. "qwen"
    if "passthrough" in sources:
        return "passthrough"
    return "unknown"


# ── comparison ────────────────────────────────────────────────────────────────


def compare_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    score_drop_threshold: float = DEFAULT_SCORE_DROP,
    latency_spike_pct: float = DEFAULT_LATENCY_PCT,
) -> RegressionReport:
    """Diff two PipelineResult dicts and classify every meaningful change."""
    b_idx, c_idx = _index(baseline), _index(candidate)

    report = RegressionReport(
        baseline_id=baseline.get("job_id", "baseline"),
        candidate_id=candidate.get("job_id", "candidate"),
        baseline_pass_rate=float(baseline.get("pass_rate", 0.0)),
        candidate_pass_rate=float(candidate.get("pass_rate", 0.0)),
        baseline_judge=_judge_mode(baseline),
        candidate_judge=_judge_mode(candidate),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        only_in_baseline=sorted(set(b_idx) - set(c_idx)),
        only_in_candidate=sorted(set(c_idx) - set(b_idx)),
    )

    shared = sorted(set(b_idx) & set(c_idx))
    report.compared = len(shared)

    for sid in shared:
        b, c = b_idx[sid], c_idx[sid]
        bv, cv = b.get("verdict", ""), c.get("verdict", "")
        bs, cs = _score_of(b), _score_of(c)
        b_ms = float(b.get("total_ms", 0.0) or 0.0)
        c_ms = float(c.get("total_ms", 0.0) or 0.0)

        delta = round(cs - bs, 2) if (bs is not None and cs is not None) else None
        lat_delta = round(c_ms - b_ms, 1)
        lat_pct = round((lat_delta / b_ms) * 100, 1) if b_ms > 0 else 0.0

        kinds: list[str] = []
        if bv == "PASS" and cv == "FAIL":
            kinds.append("flip")
        if bv == "FAIL" and cv == "PASS":
            kinds.append("fix")
        if bv == "FAIL" and cv == "FAIL":
            kinds.append("stable_fail")
        if delta is not None and delta <= -score_drop_threshold:
            kinds.append("score_drop")
        if b_ms > 0 and lat_delta > 0 and lat_pct >= latency_spike_pct:
            kinds.append("latency_spike")

        # A scenario already failing in both runs is context, not news: don't
        # let a score wobble or a slow retry promote it to a regression.
        if "stable_fail" in kinds:
            kinds = ["stable_fail"]

        if kinds:
            report.diffs.append(ScenarioDiff(
                scenario_id=sid,
                intent=c.get("intent", b.get("intent", "")),
                baseline_verdict=bv, candidate_verdict=cv,
                baseline_score=bs, candidate_score=cs, score_delta=delta,
                baseline_ms=b_ms, candidate_ms=c_ms,
                latency_delta_ms=lat_delta, latency_delta_pct=lat_pct,
                kinds=kinds,
                reason=(c.get("judgment") or {}).get("reason", ""),
                judge_source=_source_of(c),
            ))

    # scenarios that exist only in the candidate AND fail = new failures
    for sid in report.only_in_candidate:
        c = c_idx[sid]
        if c.get("verdict") == "FAIL":
            report.diffs.append(ScenarioDiff(
                scenario_id=sid, intent=c.get("intent", ""),
                baseline_verdict="(absent)", candidate_verdict="FAIL",
                candidate_score=_score_of(c),
                candidate_ms=float(c.get("total_ms", 0.0) or 0.0),
                kinds=["new_failure"],
                reason=(c.get("judgment") or {}).get("reason", ""),
                judge_source=_source_of(c),
            ))

    return report


# ── rendering helpers ─────────────────────────────────────────────────────────


def _md(text: str) -> str:
    """Make free text safe inside a Markdown table cell.

    Reasons come from the SLM and routinely contain '|' and newlines, either of
    which silently breaks the table. Nothing is truncated: the reason is the
    most useful column in the report.
    """
    return (
        (text or "")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _wrap(text: str, width: int = 88, indent: str = " " * 10) -> str:
    """Wrap a long reason for the console instead of cutting it off."""
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return ("\n" + indent).join(lines) if lines else ""


# ── rendering ─────────────────────────────────────────────────────────────────


def render_text(report: RegressionReport) -> str:
    """Console summary — what a QA engineer reads first."""
    L: list[str] = []
    verdict = "REGRESSIONS DETECTED" if report.has_regressions else "NO REGRESSIONS"
    L.append(f"=== {verdict} ===")
    L.append(f"baseline : {report.baseline_id}  ({report.baseline_pass_rate}% pass, "
             f"judge: {report.baseline_judge})")
    L.append(f"candidate: {report.candidate_id}  ({report.candidate_pass_rate}% pass, "
             f"judge: {report.candidate_judge})")
    L.append(f"pass rate delta: {report.pass_rate_delta:+.1f} points "
             f"over {report.compared} shared scenario(s)")
    if report.judge_mismatch:
        L.append("")
        L.append("!! WARNING: the two runs were judged differently "
                 f"({report.baseline_judge} vs {report.candidate_judge}). "
                 "A passthrough run only checks that execution succeeded, so "
                 "quality changes are invisible. Re-run with the same judge "
                 "before trusting this diff.")
    L.append("")

    def block(title: str, items: list[ScenarioDiff], fmt) -> None:
        if items:
            L.append(f"{title} ({len(items)})")
            for d in items:
                L.append("  " + fmt(d))
            L.append("")

    block("PASS -> FAIL flips", report.flips,
          lambda d: f"{d.scenario_id:8} {d.intent:22} "
                    f"score {d.baseline_score} -> {d.candidate_score}\n"
                    f"          {_wrap(d.reason)}")
    block("Score drops (still passing)", report.score_drops,
          lambda d: f"{d.scenario_id:8} {d.intent:22} {d.score_delta:+.2f} "
                    f"({d.baseline_score} -> {d.candidate_score})")
    block("Latency spikes", report.latency_spikes,
          lambda d: f"{d.scenario_id:8} {d.intent:22} "
                    f"{d.baseline_ms:.0f} -> {d.candidate_ms:.0f} ms ({d.latency_delta_pct:+.0f}%)")
    block("New failures", report.new_failures,
          lambda d: f"{d.scenario_id:8} {d.intent:22}\n          {_wrap(d.reason)}")
    block("Fixes (improvements)", report.fixes,
          lambda d: f"{d.scenario_id:8} {d.intent:22} FAIL -> PASS")
    block("Already failing before (unchanged)", report.stable_failures,
          lambda d: f"{d.scenario_id:8} {d.intent:22} FAIL in both runs")

    if report.only_in_baseline:
        L.append(f"only in baseline: {', '.join(report.only_in_baseline)}")
    if not report.diffs:
        L.append("No differences worth reporting.")

    L.append("")
    L.append(f"VERDICT: {report.verdict_sentence}")
    return "\n".join(L)


def render_markdown(report: RegressionReport) -> str:
    """Markdown report for the deliverable / CI artifact."""
    L = [
        "# Regression Report",
        "",
        f"- **Baseline:** `{report.baseline_id}` — {report.baseline_pass_rate}% pass "
        f"(judge: {report.baseline_judge})",
        f"- **Candidate:** `{report.candidate_id}` — {report.candidate_pass_rate}% pass "
        f"(judge: {report.candidate_judge})",
        f"- **Pass rate delta:** {report.pass_rate_delta:+.1f} points",
        f"- **Scenarios compared:** {report.compared}",
        f"- **Regressions:** {'YES' if report.has_regressions else 'none'}",
        f"- **Generated:** {report.generated_at}",
        "",
    ]
    if report.judge_mismatch:
        L += [
            f"> **Warning — the two runs were judged differently "
            f"({report.baseline_judge} vs {report.candidate_judge}).** "
            "A passthrough run only verifies that execution succeeded, so answer "
            "quality is not assessed and quality changes are invisible. Re-run "
            "both with the same judge before trusting this comparison.",
            "",
        ]
    if report.flips:
        L += ["## PASS to FAIL flips", "",
              "These scenarios passed before the change and fail now — the regressions "
              "that matter.", "",
              "| Scenario | Intent | Score | Judge | Reason |", "|---|---|---|---|---|"]
        L += [f"| {d.scenario_id} | {_md(d.intent)} | {d.baseline_score} to "
              f"{d.candidate_score} | {_md(d.judge_source)} | {_md(d.reason)} |"
              for d in report.flips]
        L.append("")
    if report.score_drops:
        L += ["## Score drops (still passing)", "",
              "Early warning: the answer still passes, but the judge rated it "
              "noticeably lower than before.", "",
              "| Scenario | Intent | Delta | Score | Reason |", "|---|---|---|---|---|"]
        L += [f"| {d.scenario_id} | {_md(d.intent)} | {d.score_delta:+.2f} "
              f"| {d.baseline_score} to {d.candidate_score} | {_md(d.reason)} |"
              for d in report.score_drops]
        L.append("")
    if report.latency_spikes:
        L += ["## Latency spikes", "",
              "| Scenario | Baseline | Candidate | Change |", "|---|---|---|---|"]
        L += [f"| {d.scenario_id} | {d.baseline_ms:.0f} ms | {d.candidate_ms:.0f} ms "
              f"| {d.latency_delta_pct:+.0f}% |" for d in report.latency_spikes]
        L.append("")
    if report.new_failures:
        L += ["## New failures", "",
              "Scenarios absent from the baseline that fail in this run.", "",
              "| Scenario | Intent | Reason |", "|---|---|---|"]
        L += [f"| {d.scenario_id} | {_md(d.intent)} | {_md(d.reason)} |"
              for d in report.new_failures]
        L.append("")
    if report.fixes:
        L += ["## Fixes (improvements)", "",
              "| Scenario | Intent | Change |", "|---|---|---|"]
        L += [f"| {d.scenario_id} | {_md(d.intent)} | FAIL to PASS |"
              for d in report.fixes]
        L.append("")
    if report.stable_failures:
        L += ["## Already failing before (unchanged)", "",
              "Pre-existing failures, identical in both runs. Listed for context so "
              "the failure count adds up — they are **not** regressions.", "",
              "| Scenario | Intent | Reason |", "|---|---|---|"]
        L += [f"| {d.scenario_id} | {_md(d.intent)} | {_md(d.reason)} |"
              for d in report.stable_failures]
        L.append("")
    if report.only_in_baseline:
        L += ["## Missing from this run", "",
              "In the baseline but not executed here — coverage shrank.", "",
              ", ".join(f"`{s}`" for s in report.only_in_baseline), ""]

    L += ["---", "", f"**Verdict:** {report.verdict_sentence}", ""]
    return "\n".join(L)


def to_dict(report: RegressionReport) -> dict[str, Any]:
    d = asdict(report)
    d["has_regressions"] = report.has_regressions
    d["pass_rate_delta"] = report.pass_rate_delta
    d["judge_mismatch"] = report.judge_mismatch
    d["verdict_sentence"] = report.verdict_sentence
    return d