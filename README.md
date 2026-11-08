> [!NOTE]
> **Branch for Reviewer 5Xuf, W1 / Q1.** This branch adds the run logs of `FactorRuleFilter` under the main-table protocol. The code is the same as `main`. Everything below the line is the original README.

## Reviewer 5Xuf, W1: comparison with `FactorRuleFilter`

> **W1:** The main experiments primarily compare against TTL and Bayesian-Agent, neither of which exploits shared subsystem information in the same way. The repository includes `FactorRuleFilter`, a simpler non-Bayesian method that propagates suspected invalidity across related memories, but it is absent from the main results.
>
> **Q1:** How does Bayesian-Harness compare with `FactorRuleFilter` and simpler subsystem-aware heuristics under the same budgets?

**Response.** Thank you for taking the time to read our code. `FactorRuleFilter` is an intermediate step in the design of Bayesian-Harness, not a full method, which is why it was not in the paper. Following your suggestion, we have now evaluated it on all three Qwen3.5 model sizes under the main-table protocol.

<p align="center"><img src="rebuttal/W1_FactorRuleFilter/w1_factor_rule.gif" alt="FactorRuleFilter vs. other policies on MaskedMig" width="950"></p>

| (MaskedMig, budget 24, 3 seeds) | Qwen3.5-4B | Qwen3.5-9B | Qwen3.5-27B |
| :--- | :---: | :---: | :---: |
| Oracle | 27.4 ± 1.9 | 38.9 ± 2.5 | 52.2 ± 1.3 |
| None | 0.6 ± 0.6 | 0.6 ± 1.1 | 12.1 ± 0.6 |
| Static | 0.6 ± 0.0 | 1.3 ± 2.2 | 8.9 ± 8.3 |
| Random | 16.6 ± 5.1 | 21.0 ± 8.0 | 37.6 ± 1.7 |
| Bayesian-Agent | 14.0 ± 4.8 | 18.5 ± 2.3 | 36.3 ± 7.8 |
| TTL | 17.8 ± 3.9 | 22.9 ± 2.3 | 40.1 ± 7.6 |
| `FactorRuleFilter` (new) | 14.9 ± 2.0 | 20.0 ± 2.0 | 39.3 ± 2.9 |
| **Bayesian-Harness (ours)** | **25.5 ± 1.7** | **29.9 ± 2.2** | **48.4 ± 2.8** |

`FactorRuleFilter` also shares suspicion across a subsystem, yet it stays close to TTL and 9 to 11 points below our method. So the gain comes mainly from modelling validity and quality, not just from sharing subsystem information.

### Files

| Path | Content |
| :--- | :--- |
| [`rebuttal/W1_FactorRuleFilter/logs/`](rebuttal/W1_FactorRuleFilter/logs) | `mig_*`: MaskedMig runs (3 models x 3 seeds). `acme_*`: AcmeSDK runs at budgets 12 / 24 / 48. `vllm_*`: model server logs. |
| [`rebuttal/W1_FactorRuleFilter/make_gif.py`](rebuttal/W1_FactorRuleFilter/make_gif.py) | Draws the GIF above. |
| [`bayesian_harness/filters.py`](bayesian_harness/filters.py) | `FactorRuleFilter` (registry name `factor_rule`). |

```bash
# reproduce one cell (needs a model server, see scripts/serve_vllm.sh)
LANES=factor_rule scripts/run_maskedmig.sh Qwen/Qwen3.5-27B 24 1
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
