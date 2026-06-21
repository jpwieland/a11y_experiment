# Re-run V5 (functional check) + corrected report — checklist

This is a step-by-step guide to **re-run the architectural-ablation V5 condition
(`arch_no_internal_validation`) with the new Layer-2 functional check**, on the
machine that holds the corpus, and regenerate the corrected report.

## Why this re-run is needed

- V5 bypasses the validation pipeline as a **gate**, but the runner now also
  **measures** Layer-2 (structural functional preservation) without enforcing
  it. This lets us tell whether a Pa11y-credited fix also **broke the
  component** (e.g. dropped an export / prop / handler).
- The original V5 runs predate this instrumentation, so the fields
  `functional_regression` / `resolved_functional_clean` are absent and must be
  regenerated.
- Only **V5** needs re-running. For the other four conditions Layer 2 was an
  enforced gate, so `resolved_functional_clean == resolved` by construction and
  the report fills that in automatically.

> The report also now uses **file-level pairing** (n = number of files) instead
> of violation-level pairing pooled across reps (which inflated n to 681 and the
> significance). This is a pure analysis change and already applies to existing
> data — no re-run needed for it.

## Prerequisites (must be the corpus machine)

- [ ] `dataset/snapshots/` is **populated** (the cloned project source files).
      A results-only checkout will NOT work — the runner reads each file's
      original content from here.
- [ ] `dataset/results/` has the `scan_results.json` files.
- [ ] `experiments/chosen_experiment.yaml` present (project selection).
- [ ] Python ≥ 3.11.
- [ ] Ollama installed, server running, and the model pulled:
      `ollama serve` (in another shell) and `ollama pull qwen2.5-coder:7b`.
- [ ] Pa11y / Chromium harness working (the main pipeline must be able to scan;
      same dependency as the main experiment's re-scan oracle).

## Steps

### 1. Pull the latest code
```bash
cd /path/to/a11y_experiment        # the repo root (where pyproject.toml lives)
git checkout main
git pull origin main
```

### 2. Environment
```bash
# core package + ablation analysis deps
pip install -e .
pip install -r ablation_study/requirements_ablation.txt
```
(Or activate the project's existing `.venv` — the launcher auto-sources
`.venv/bin/activate` if present.)

### 3. Sanity check (no compute)
```bash
./run_arch_v5_functional.sh --dry-run
```
Confirms the corpus/Ollama pre-flight passes and prints the planned cells
(should be `arch_no_internal_validation` × `qwen2.5-coder-7b` × reps 1–3).

### 4. Re-run V5 + rebuild report
```bash
./run_arch_v5_functional.sh
```
What it does:
1. Backs up the old V5 results to
   `ablation_study/results/arch_ablation_pipeline/arch_no_internal_validation.pre_functional.<timestamp>/`
   then removes the live dir so the runner regenerates it.
2. Runs `arch_ablation_pipeline_runner.py` for V5, all 3 reps
   (seeds 42/137/2025). Expect roughly **20–25 min per rep** (~1.0–1.2 h total)
   on the reference hardware; depends on GPU/Ollama throughput.
3. Runs `python -m ablation_study.analysis.arch_report` to rebuild the report
   with file-level pairing + the functional-clean decomposition.

> Resumable: if interrupted, just re-run `./run_arch_v5_functional.sh`
> — but note it backs up + wipes V5 again at the start. To resume a partially
> completed re-run instead, skip the script and call the runner directly
> (it skips cells whose `summary.json` already exists):
> ```bash
> python -m ablation_study.src.arch_ablation_pipeline_runner \
>   --config ablation_study/config/arch_ablation_pipeline_config.yaml \
>   --condition arch_no_internal_validation --model qwen2.5-coder-7b
> ```
> then `./run_arch_v5_functional.sh --report-only`.

### 5. Verify the functional check populated
```bash
grep has_functional ablation_study/results/arch_ablation_pipeline/report/functional_clean_ifr.json
# expect: "has_functional_field": true
```
And confirm the analysis unit:
```bash
python -c "import json;print(json.load(open('ablation_study/results/arch_ablation_pipeline/report/stats_results.json'))['metadata']['pairing_unit'])"
# expect: file
```

### 6. What to read
- `report/ablation_table_ifr.tex` — IFR per condition (file-level macro-avg + 95% CI).
- `report/ablation_table_tests.tex` — Wilcoxon vs `arch_full`, Holm-Bonferroni.
  **Caveat:** ignore the `Power` column at this n — the Noether approximation
  overstates power when only a handful of file pairs are non-tied.
- `report/arch_table_functional_clean.tex` + `functional_clean_ifr.json` — the
  NEW decomposition. **The headline number:** for `arch_no_internal_validation`,
  how far below the raw IFR (~0.90) does the functional-clean IFR fall? That gap
  is the share of "fixes" that broke the component — i.e. how much the validation
  pipeline's apparent over-rejection is actually catching real regressions.

## Optional: full re-run of all 5 conditions

Re-running only V5 is enough for the functional decomposition. If you also want
the functional fields stored uniformly across every condition (cosmetic — values
are unchanged for the gated conditions), run the whole study:
```bash
python -m ablation_study.src.arch_ablation_pipeline_runner \
  --config ablation_study/config/arch_ablation_pipeline_config.yaml --reset
python -m ablation_study.analysis.arch_report
```
This is ~5–6 h of compute. Not required for the corrected conclusions.

## Statistical-power note (read before drawing conclusions)

With file-level pairing the current data shows **no condition significant after
Holm-Bonferroni** (n = 66). The study is **underpowered**: most files are ties.
Even after the V5 functional check, expect the same — the functional decomposition
is descriptive (how many V5 fixes were clean), not a new significance test. To get
real power, increase `subset_size`/add projects in
`arch_ablation_pipeline_config.yaml` and re-run all conditions.
