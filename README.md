<h1 align="center">Reviewer zpd8 · W1, Q2</h1>

<p align="center">
  <img alt="Reviewer: zpd8" src="https://img.shields.io/badge/Reviewer-zpd8-3C5488">
  <img alt="Points: W1 Q2" src="https://img.shields.io/badge/Points-W1_Q2-E64B35">
  <img alt="Policy: two_counters" src="https://img.shields.io/badge/Policy-two__counters-00A087">
  <img alt="Run logs: 9" src="https://img.shields.io/badge/Run_logs-9-8250DF">
  <img alt="Code: unchanged" src="https://img.shields.io/badge/Code-unchanged-0969DA">
</p>

> [!NOTE]
> **Branch for Reviewer zpd8, weakness 1 and question 2.** It adds the run logs of the two-counter policy (Bayesian-Agent's
> Beta posterior plus a TTL clock and a per-library success rate) under the MaskedMig main-table protocol, 3 models × 3 seeds.
> The code is the same as `main`; `TwoCountersFilter` has been in `bayesian_harness/filters.py` since the submission.

## At a glance

| Reviewer's point | What this branch adds | Jump to |
|:---|:---|:---|
| **W1** the feature-free reduction of Bayesian-Agent does not evaluate its full method; please compare with the two-counter policy in the main setting | the two-counter policy under the main-table protocol | [Results](#the-concern) |
| **W1, Q2** distinguish persistent API changes from the probabilistic failures of ResiliBench | the answer, with the deterministic probe | [Response](#the-concern) |

---

## The concern

> **W1:** Section 4.3 reduces Bayesian-Agent [BA26] to a feature-free Beta posterior, so the comparison does not evaluate its full method. I would appreciate a main-setting comparison with the two-counter policy already used in Table 3. Please also distinguish persistent API changes from the probabilistic execution failures studied in ResiliBench [RB26]; this would clarify which failures the validity–quality model is intended to separate.
>
> **Q2:** Are main-setting two-counter results available, and which shared features could [BA26] use? Relative to [RB26], how does a probe distinguish a persistent API change from a transient execution failure?

**Response.** On Bayesian-Agent: it conditions its posterior on context features, but our notes carry no such features, so we keep one Beta posterior per note. We have updated the paper to state this explicitly. The two-counter policy can be seen as a stronger version of it that adds a time clock and a group success rate. Following the suggestion, we ran it under the main-table protocol of MaskedMig.

| (MaskedMig, budget 24, 3 seeds) | Qwen3.5-4B | Qwen3.5-9B | Qwen3.5-27B |
|:---|:---:|:---:|:---:|
| Bayesian-Agent (paper) | 14.0 ± 4.8 | 18.5 ± 2.3 | 36.3 ± 7.8 |
| TTL (paper) | 17.8 ± 3.9 | 22.9 ± 2.3 | 40.1 ± 7.6 |
| Two counters (new) | `16.3 ± 1.6` | `20.4 ± 2.3` | `38.0 ± 3.3` |
| **Bayesian-Harness (ours, paper)** | **25.5 ± 1.7** | **29.9 ± 2.2** | **48.4 ± 2.8** |

Two counters performs close to TTL and stays 9 to 10 points below our method at every model size.

On ResiliBench: it looks at random failures; we look at lasting changes. We make the environment deterministic. The probe runs the note's code against the library version of that period, so a failed probe always means a persistent API change, and we update the posterior of Validity. In our setting the randomness comes from the agent, and we model it with Quality. Our method splits the cause of a failure into these two factors: Validity asks whether the API has changed, Quality asks whether the note itself is good. Random execution failures are not part of our setting.

<p align="center"><img src="rebuttal/zpd8_W1_two_counters/w1_two_counters.gif" alt="Two counters vs. Bayesian-Harness on MaskedMig" width="900"></p>

<details>
<summary><strong>What the two-counter policy does</strong></summary>

`TwoCountersFilter` (registry name `two_counters`, [`bayesian_harness/filters.py`](bayesian_harness/filters.py)) keeps, per note, the Beta posterior of Bayesian-Agent and a TTL clock, and per library a running success rate. A note is ranked for probing by the TTL decay of its posterior mean, and it is pushed up when its library's success rate falls below the mean of all libraries. It has time and group information; it has no latent validity, no quality variable and no recovery model.

</details>

## What this branch adds

```text
rebuttal/zpd8_W1_two_counters/
  logs/mig_q{4b,9b,27b}_s{1,2,3}.log     9 MaskedMig lanes, one line per quarter, final per-condition block
  make_gif.py, style.py                  draw the GIF above
```

## Reproduce

```bash
LANES=two_counters scripts/run_maskedmig.sh Qwen/Qwen3.5-27B 24 1     # needs a model server and the migration environments
```

## Integrity

- The *new* row above is recomputed from the per-quarter lines of the nine logs (pooled pass rate over the three seeds, 157 migrations each, s.d. across seeds).

## Environment of the new runs

| | |
|:---|:---|
| Inference | vLLM 0.30.0 (PyTorch 2.13.0+cu130, transformers 5.22.1), one NVIDIA H200, `--max-model-len 32768`, `--gpu-memory-utilization` 0.92 (27B) / 0.90 (4B, 9B) |
| Models | Qwen3.5-4B `851bf6e8`, Qwen3.5-9B `c2022362`, Qwen3.5-27B `fc05daec` (Hugging Face snapshot prefixes) |
| Code | `main` of this repository plus the changes listed above |
| Protocol | identical flags to the paper's main tables; only the policy list differs |
| Anonymization | paths, user and host names in logs and result files were replaced by `$REPO`, `$HOME`, `user`, `node`; nothing else was edited |

The rebuttal runs used a different machine and inference-engine version from the runs behind the paper's tables, so a rerun of a paper row on this stack can differ from the printed value by a few points. Rows marked *paper* in the tables above are the printed values; rows marked *new* are from the files in this branch.

## Other rebuttal branches

Every branch starts from `main`, adds only what the reviewer asked for, and carries its own README.

| Branch | Reviewer | Points | Content |
|:---|:---:|:---:|:---|
| [`reviewer_5Xuf/W1_FactorRuleFilter`](../../tree/reviewer_5Xuf/W1_FactorRuleFilter) | 5Xuf | W1, Q1 | `FactorRuleFilter` (sibling-propagation rule) under both main-table protocols, 36 run logs |
| [`reviewer_5Xuf/W2_exact`](../../tree/reviewer_5Xuf/W2_exact) | 5Xuf | W2, Q2 | exact period-by-period staleness update next to the first-order form used in the paper |
| [`reviewer_zpd8/Q1_failed_repair`](../../tree/reviewer_zpd8/Q1_failed_repair) | zpd8 | Q1 | what happens after a failed repair: reset (paper) vs. keep-as-broken-and-retry, 18 run logs |
| [`reviewer_zpd8/W1_two_counters`](../../tree/reviewer_zpd8/W1_two_counters) **(this branch)** | zpd8 | W1, Q2 | two-counter policy under the MaskedMig main-table protocol, 9 run logs |
| [`reviewer_zpd8/W2_oracle_backoff`](../../tree/reviewer_zpd8/W2_oracle_backoff) | zpd8 | W2 | Oracle with back-off after a failed repair, 27 run logs |
| [`reviewer_TvTY/W1_release_signal`](../../tree/reviewer_TvTY/W1_release_signal) | TvTY | W1, Q1, Q2, Q3 | release-aware baseline (version bump / changelog), sensitivity sweep on the reviewer's grid, calibration data |
| [`reviewer_TvTY/W2_paired_tests`](../../tree/reviewer_TvTY/W2_paired_tests) | TvTY | W2, Q4, Q5, Q6 | paired McNemar tests for every main-table cell, low-quality-notes world, pointer to the Oracle ceiling |

The full code release and its documentation are on the [`main`](../../tree/main) branch. The data (19 generated worlds, 157 masked migrations) is in the companion [bayesian_harness_datasets](https://github.com/anonymouspaper987/bayesian_harness_datasets) repository.
