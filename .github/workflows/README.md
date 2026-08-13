# Continuous integration

Two workflows, because they have very different needs.

| Workflow | Runner | Runs | Guards |
|---|---|---|---|
| `tests.yml` | GitHub-hosted | every push and PR | the QA pipeline's own code (71 unit tests) |
| `qa-regression.yml` | **self-hosted** | manual or nightly | LaRuche itself — fails when the chatbot gets worse |

## Why two

A full pipeline run needs three uvicorn services, the Vite frontend, Chrome and
Ollama with `qwen2.5:3b` on a GPU. GitHub's hosted runners have none of that,
and CPU-only inference would take far too long. So the heavy gate runs on a
machine that already has the stack — yours, or a lab box — while the fast unit
tests stay on free hosted runners where they belong.

Splitting them also keeps feedback quick: a broken import is caught in under a
minute on every PR, without waiting on a browser.

---

## Files to add

```
.github\workflows\tests.yml
.github\workflows\qa-regression.yml
autonomous\graph\latest_run.py      <-- helper the gate needs
```

`latest_run.py` exists because job folders are timestamped
(`runs/job_20260811_024912`), so the workflow cannot hard-code the path of the
run it just produced. It prints the newest one and exits 1 if there is none, so
a failed run cannot silently compare against a stale file.

Try it locally:

```
python -m autonomous.graph.latest_run
```

---

## Committing a baseline

The gate compares against a baseline stored **in the repository**, so it must be
committed:

```
mkdir baselines\v1_judged
xcopy runs\job_20260811_022717 baselines\v1_judged\ /E /I
git add baselines\v1_judged\pipeline_result.json
git commit -m "QA baseline: LaRuche v1, judged run, 61.5% pass"
```

> Record the baseline from a run made **with `--ollama-judge`**. A passthrough
> run only proves the browser steps completed — quality regressions would be
> invisible. The comparator now warns loudly if the two runs were judged
> differently, but the right fix is not to create the situation.

Re-record the baseline deliberately, never automatically: a baseline that
updates itself can absorb a regression and hide it forever.

---

## Setting up the self-hosted runner

On the machine that runs LaRuche:

1. In the GitHub repo: **Settings → Actions → Runners → New self-hosted runner**,
   choose Windows, and follow the download-and-configure commands shown.
2. Run it: `run.cmd` (foreground), or install it as a service with
   `svc.cmd install` then `svc.cmd start` so it survives reboots.
3. Confirm the runner shows **Idle** in the repo's Runners page.

The runner inherits the environment of whatever launched it, so before starting
it in a terminal, set the same variables the pipeline needs:

```
set PYTHONPATH=C:\laruche\intervalue-main\libs\agentkit\src
```

A self-hosted runner executes any workflow in the repo. Keep the repository
private, or restrict the runner to protected branches.

---

## Running the gate

Manually — **Actions → QA regression gate → Run workflow**. You can override the
frontend URL, the baseline path, and the score-drop threshold from that form.

Nightly — already scheduled at 03:00 UTC. The machine must be on with LaRuche
and Ollama running, otherwise the preflight step fails the job with a clear
message rather than reporting thirteen false regressions.

### What it does

1. Checks the baseline file exists.
2. Preflights the SUT: frontend, ports 8000/8001/8002, and Ollama on 11434.
   If anything is down it stops immediately — an unreachable chatbot would fail
   every scenario and look like a catastrophic regression.
3. Runs the pipeline with the SLM judge.
4. Finds the run that just finished and compares it to the baseline.
5. Writes `regression.md` into the **job summary**, so the report is readable on
   the run page without downloading anything.
6. Uploads `regression.md`, `regression.json` and `runs/` as artifacts (30 days).
7. Fails the build if regressions were found.

The report is uploaded even on failure — that is the whole point, since a failed
gate is exactly when you want to read it.

---

## Using it as a merge gate

Once the workflow has run at least once, make it required:
**Settings → Branches → branch protection rule → Require status checks to pass**,
then select `Tests` (and `QA regression gate` if you want the heavy check
blocking too).

For a stage demo, requiring only `Tests` and running the gate nightly is the
honest setup: a browser-driven, model-judged run is too slow and too
non-deterministic to sit in front of every merge.

---

## Known limitation, worth stating in the report

The judge is `qwen2.5:3b`, and small models give borderline verdicts that can
flip between identical runs. A scenario sitting near the pass threshold may
therefore be reported as a regression on judge variance alone.

Two mitigations, both already scoped in the v2 proposal: run sensitive scenarios
several times and require the failure to repeat, and let deterministic rules
decide everything objective so the model only arbitrates genuinely ambiguous
cases. Until then, treat a single-scenario regression as a signal to look, not
as proof.
