> [!NOTE]
> **Branch for Reviewer 5Xuf, W2 / Q2.** This branch adds the exact period-by-period update of Eq. 8 next to the first-order approximation used for the paper's numbers. Code only, no run logs. Everything below the line is the original README.

## Reviewer 5Xuf, W2: approximation in `_touch_entry`

> **W2:** Equation 7 and Appendix A.1 describe updating staleness period by period as the regime belief evolves. However, the released `_touch_entry` implementation appears to multiply the one-period hazard, evaluated using the final regime belief, by the number of elapsed periods.
>
> **Q2:** Is the multi-period update in `_touch_entry` an intentional approximation? How does it affect the results compared with the update described in the paper?

**Response.** Thank you for pointing this out. Your observation is correct. Our implementation uses a first-order approximation, and it only affects the MaskedMig benchmark.

1. **We ran the exact period-by-period update.** We re-ran our method on MaskedMig with the update exactly as written in the paper, under the main-table protocol.

   | (MaskedMig, Qwen3.5-27B, budget 24, 5 seeds) | Accuracy (%) |
   | :--- | :---: |
   | First-order approximation (used in the paper) | 48.5 ± 2.2 |
   | Exact period-by-period update | 46.9 ± 4.0 |

   The two differ by only 1.6 points, well within the seed-to-seed spread (about 4 points), and make nearly the same maintenance decisions. Our conclusions are unchanged.

2. **Where the approximation matters.** On AcmeSDK the filter is updated every month, so each update spans exactly one period and the two versions are identical. Only MaskedMig, where one update spans a quarter (three months), is affected.

3. **We will revise the paper.** We will revise Eq. 8 and Appendix A.1 to describe the approximation actually used, and add this comparison to the appendix.

4. **We release both versions** in this branch.

<p align="center"><img src="rebuttal/W2_exact/w2_exact.gif" alt="Exact vs. first-order staleness update over a 3-month gap" width="950"></p>

The GIF shows one note over a 3-month gap. The exact update compounds once per month with the belief at that month. The approximation takes one step with the belief at the end of the gap, so a burst early in the gap counts for less. Individual scores can move, but the ranking of notes, and so the maintenance decisions, stay nearly the same.

### What changed in the code

| File | Change |
| :--- | :--- |
| [`bayesian_harness/filters.py`](bayesian_harness/filters.py) | New `FilterConfig.compose_per_period` (default `0` = approximation, paper numbers). With `1`, `_touch_entry` calls `_compose_entry`, which applies Eq. 8 once per period with the regime belief `b(tau)` from Eq. 7. |
| [`scripts/run_maskedmig_w2_exact.sh`](scripts/run_maskedmig_w2_exact.sh) | Runs both versions on MaskedMig (budget 24, `--t-scale 3`). |
| [`rebuttal/W2_exact/make_gif.py`](rebuttal/W2_exact/make_gif.py) | Draws the GIF above. |

```bash
# reproduce (needs a model server, see scripts/serve_vllm.sh)
scripts/run_maskedmig_w2_exact.sh Qwen/Qwen3.5-27B "1 2 3 4 5"
```

---

# Bayesian-Harness

<p align="center">
  <strong>Probabilistic Knowledge Control for LLM Agents</strong><br>
  <sub>Anonymous code release</sub>
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="License MIT" src="https://img.shields.io/badge/License-MIT-2ea44f">
  <img alt="LLM optional" src="https://img.shields.io/badge/LLM-Optional-orange">
  <img alt="OpenAI-compatible API" src="https://img.shields.io/badge/API-OpenAI--compatible-412991?logo=openai&logoColor=white">
  <img alt="19 generated worlds" src="https://img.shields.io/badge/Worlds-19-0969DA">
  <img alt="157 masked migrations" src="https://img.shields.io/badge/Migrations-157-8250DF">
</p>

LLM agents keep notes about changing libraries—but notes become stale. **Bayesian-Harness** decides which notes to check and rewrite under a limited maintenance budget.

It models two separate properties for every note:

- **Validity** — is the note still current?
- **Quality** — how likely is the note to help?

> **Code is here; data lives next door.** The companion [bayesian_harness_datasets](https://github.com/anonymouspaper987/bayesian_harness_datasets) repository contains 19 generated worlds and 157 masked API migrations.

```bash
git clone https://github.com/anonymouspaper987/bayesian_harness_datasets data
```

## Quick start

No language model required:

```bash
# 1. See why validity and quality must be modeled separately
python3 acmesdk/toy_quality.py
python3 acmesdk/recover_2x2.py

# 2. Build a synthetic world (~10 s)
scripts/build_world.sh 1

# 3. Compare maintenance policies (~2 s)
python3 acmesdk/offline_eval.py \
  --seeds 1 \
  --conds ours_hhmm,bayesian_agent,ttl,random \
  --reps 2
```

The evaluation reports task success, note coverage, probe cost, repair count, and probe hit rate.

## Repository map

```text
bayesian_harness/   Bayesian model, baselines, and filter registry
acmesdk/            Synthetic SDK world, agent loop, and analysis
maskedmig/          157 masked real-API migrations
scripts/            Build, serve, and run helpers
```

Start with [`bayesian_harness/filters.py`](bayesian_harness/filters.py). `build_filter()` contains the full policy registry.

## Methods

| Method | CLI name | Implementation |
|---|---|---|
| **Bayesian-Harness** | `ours_hhmm` | `HierarchicalHMMFilter` |
| Bayesian-Agent | `bayesian_agent` | `BayesianAgentFilter` |
| TTL | `ttl` | `TTLFilter` |
| Random | `random` | `RandomFilter` |
| Two counters | `two_counters` | `TwoCountersFilter` |
| Oracle | `oracle` | Free release-note access |
| Static / none | `static`, `none` | No maintenance / no notes |

<details>
<summary><strong>View ablation names</strong></summary>

| Ablation | CLI name | Config field |
|---|---|---|
| No burst recovery | `ours_hhmm_no_recover` | `hmm_recover` |
| No shared factors | `ours_hhmm_no_factor` | `use_factors` |
| No subsystem pooling | `ours_hhmm_no_pool` | `pool_kappa` |
| No error channel | `ours_hhmm_no_error_channel` | `use_error_channel` |
| Fixed hazard | `ours_hhmm_fixed_lambda` | `hazard_learn` |
| Validity only | `ours_hhmm_validity_only` | `fixed_pbar` |

</details>

## Run with a real model

One **cell** runs one model, seed, and monthly budget across all policies and 36 months.

```bash
scripts/serve_vllm.sh Qwen/Qwen3.5-27B 8000
scripts/build_world.sh 1
TAG=qwen27b scripts/run_acmesdk.sh Qwen/Qwen3.5-27B 1 24

python3 acmesdk/analyze.py --seed 1 --prefix qwen27b_mb24
python3 acmesdk/summarize_hhmm.py
python3 acmesdk/paired_tests.py
```

Results are written to `data/acmesdk/seed1/results/` as JSONL files. The summarizer safely reports missing cells on a fresh clone.

<details>
<summary><strong>Gemini / Vertex AI</strong></summary>

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
scripts/run_acmesdk.sh gemini-2.5-flash-lite 1 24
```

Model names beginning with `gemini` use Vertex AI. Set `GEMINI_THINKING` to override the default low thinking level.

</details>

<details>
<summary><strong>Variant worlds</strong></summary>

```bash
scripts/build_world.sh 1 _noburst --n-vertical 0 --n-horizontal 0
scripts/build_world.sh 1 _dense   --n-vertical 12
scripts/build_world.sh 1 _fixed   --vmode fixed
scripts/build_world.sh 1 _htail   --vmode heavytail
scripts/build_world.sh 1 _kern    --n-kernel 6

TAG=qwen27b scripts/run_acmesdk.sh Qwen/Qwen3.5-27B 1 24 _kern
python3 acmesdk/analyze_robust.py \
  --worlds fixed,htail,kern --seeds 1 --conds ours_hhmm,ttl,oracle
```

</details>

## Masked real-API migrations

The dataset supplies the corpus; each exam also needs an isolated environment for a real library release.

```bash
export MIG_ENVS=/somewhere/with/space   # requires tens of GB
python3 maskedmig/build_fresh_envs.py

scripts/serve_vllm.sh Qwen/Qwen3.5-27B 8000
for s in 1 2 3; do
  TAG=qwen27b scripts/run_maskedmig.sh Qwen/Qwen3.5-27B 24 $s
done
python3 acmesdk/mig_abl_summary.py --prefix qwen27b
```

Successor API names are replaced by random hashes in the package, changelog, and exam. This prevents models from solving migrations from pre-training memory alone.

<details>
<summary><strong>Rebuild the migration corpus</strong></summary>

```bash
python3 maskedmig/fresh_pipeline.py mine
python3 maskedmig/fresh_pipeline.py gen
python3 maskedmig/hunt_successors.py
python3 maskedmig/verify_successors.py
```

These stages write to `$MASKEDMIG_DATA` and require the environments above.

</details>

## Setup

- **Python 3.12** is required for released worlds.
- The core harness uses only the standard library.
- `pip install -r requirements.txt` installs optional Gemini and review dependencies.
- Any OpenAI-compatible server can replace vLLM.

<details>
<summary><strong>Environment variables</strong></summary>

| Variable | Default | Purpose |
|---|---|---|
| `ACMESDK_DATA` | `data/acmesdk` | Generated worlds |
| `ACMESDK_SCRATCH` | system temp | Per-exam scratch space |
| `ACME_WORLD_SUFFIX` | none | Select a variant world |
| `MASKEDMIG_DATA` | `data/maskedmig` | Migration corpus |
| `MIG_ENVS` | `data/mig_envs` | Built package environments |
| `MIG_SCRATCH` | system temp | Build and unpack space |
| `GITCHAMELEON_DIR` | `data/gitchameleon` | Optional mining input |
| `RUN_LOG_DIR` | `logs` | Run logs |

</details>

## Not included

- Run outputs and logs
- Large per-library `maskedmig` environments
- The optional GitChameleon release and manual-review sheet

Synthetic worlds can be rebuilt with `scripts/build_world.sh`. Rebuilt worlds reproduce the API surface, drift events, and task pool; monthly exam samples may differ from the released copy.

## License

MIT. Source comments are primarily in Chinese; references to `IDEA Sec 3.x` correspond to Section 3 of the paper.
