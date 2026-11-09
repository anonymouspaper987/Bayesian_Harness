<h1 align="center">Reviewer 5Xuf · W1, Q1</h1>

<p align="center">
  <img alt="Reviewer: 5Xuf" src="https://img.shields.io/badge/Reviewer-5Xuf-3C5488">
  <img alt="Points: W1 Q1" src="https://img.shields.io/badge/Points-W1_Q1-E64B35">
  <img alt="Policy: factor_rule" src="https://img.shields.io/badge/Policy-factor__rule-00A087">
  <img alt="Run logs: 9" src="https://img.shields.io/badge/Run_logs-9-8250DF">
  <img alt="Code: unchanged" src="https://img.shields.io/badge/Code-unchanged-0969DA">
</p>

> [!NOTE]
> **Branch for Reviewer 5Xuf, weakness 1 and question 1.** It adds the run logs of `FactorRuleFilter`, the non-Bayesian
> rule that propagates suspected invalidity to every note of the same subsystem, under the exact protocol of the paper's
> main table (MaskedMig, budget 24), three model sizes, three seeds each. The code is the same as `main`;
> `FactorRuleFilter` has been in `bayesian_harness/filters.py` since the submission.

## At a glance

| Reviewer's point | What this branch adds | Jump to |
|:---|:---|:---|
| **W1** the repository's `FactorRuleFilter` is absent from the main results | the policy run under the main-table protocol, 9 runs | [Results](#results) |
| **Q1** how does it compare with simpler subsystem-aware heuristics under the same budgets | the MaskedMig table next to the paper's rows; a second subsystem-aware heuristic (two counters) in a sibling branch | [Results](#results), [Related](#other-rebuttal-branches) |

---

## The concern

> **W1:** **Comparison with baselines.** The main experiments primarily compare against TTL and Bayesian-Agent, neither of which exploits shared subsystem information in the same way. The repository includes `FactorRuleFilter`, a simpler non-Bayesian method that propagates suspected invalidity across related memories, but it is absent from the main results.
>
> **Q1:** How does Bayesian-Harness compare with `FactorRuleFilter` and simpler subsystem-aware heuristics under the same budgets?

**Response.** We sincerely thank you for taking the time to look at our code repository. `FactorRuleFilter` is part of our design, not a full method framework, which is the main reason we didn't compare it in our paper, but we left it in a branch of our repository. Following your suggestion, we have now fully evaluated `FactorRuleFilter` across all three Qwen3.5 model sizes, under the same budgets and protocols as the main tables.

## Results

**MaskedMig, the paper's main table** (budget 24 per quarter, 157 real API migrations, 3 seeds):

| (MaskedMig, budget 24, 3 seeds) | Qwen3.5-4B | Qwen3.5-9B | Qwen3.5-27B |
|:---|:---:|:---:|:---:|
| Oracle (paper) | 27.4 ± 1.9 | 38.9 ± 2.5 | 52.2 ± 1.3 |
| None (paper) | 0.6 ± 0.6 | 0.6 ± 1.1 | 12.1 ± 0.6 |
| Static (paper) | 0.6 ± 0.0 | 1.3 ± 2.2 | 8.9 ± 8.3 |
| Random (paper) | 16.6 ± 5.1 | 21.0 ± 8.0 | 37.6 ± 1.7 |
| Bayesian-Agent (paper) | 14.0 ± 4.8 | 18.5 ± 2.3 | 36.3 ± 7.8 |
| TTL (paper) | 17.8 ± 3.9 | 22.9 ± 2.3 | 40.1 ± 7.6 |
| `FactorRuleFilter` (new) | `14.9 ± 2.0` | `20.0 ± 2.0` | `39.3 ± 2.9` |
| **Bayesian-Harness (ours, paper)** | **25.5 ± 1.7** | **29.9 ± 2.2** | **48.4 ± 2.8** |

From the table, the benefit comes mainly from modelling validity and quality, not from sharing the subsystem information alone. `FactorRuleFilter` shares suspicion across a subsystem but stays near TTL.

<p align="center"><img src="rebuttal/W1_FactorRuleFilter/w1_factor_rule.gif" alt="FactorRuleFilter vs. other policies on MaskedMig" width="950"></p>

<details>
<summary><strong>What the rule does</strong></summary>

`FactorRuleFilter` keeps a TTL clock per note. When a probe or an exam shows that a note is broken, it raises the stale score of every other note of the same subsystem (AcmeSDK) or library (MaskedMig) by a fixed amount, so siblings are probed next. There is no posterior, no quality variable and no recovery model; the propagation is a rule. Registry name `factor_rule`, class `FactorRuleFilter` in [`bayesian_harness/filters.py`](bayesian_harness/filters.py).

</details>

## What this branch adds

```text
rebuttal/W1_FactorRuleFilter/
  logs/mig_q{4b,9b,27b}_s{1,2,3}.log            9 MaskedMig runs, one line per quarter, final per-condition block
  make_gif.py                                    draws the GIF above
```

## Reproduce

```bash
# one MaskedMig cell (needs a model server, see scripts/serve_vllm.sh, and the migration environments)
LANES=factor_rule scripts/run_maskedmig.sh Qwen/Qwen3.5-27B 24 1
```

## Reading the logs

One line per quarter, for example `[factor_rule] 2025Q4: 7 题 4 解出, 已修复笔记 15, probes 6 repairs 5(ok 4), 48s`
reads: quarter 2025Q4, 7 exams, 4 passed, 15 notes repaired so far, 6 probes, 5 repairs of which 4 passed verification.
The final block of every log is the per-condition summary.

## Integrity

- The *new* row above is recomputed from the per-quarter lines of the nine logs (pooled pass rate over the three seeds, 157 migrations each, s.d. across seeds).
- Every run exited with code 0.

## Environment of the new runs

| | |
|:---|:---|
| Inference | vLLM 0.30.0 (PyTorch 2.13.0+cu130, transformers 5.22.1), one NVIDIA H200, `--max-model-len 32768`, `--gpu-memory-utilization` 0.92 (27B) / 0.90 (4B, 9B) |
| Models | Qwen3.5-4B `851bf6e8`, Qwen3.5-9B `c2022362`, Qwen3.5-27B `fc05daec` (Hugging Face snapshot prefixes) |
| Code | `main` of this repository plus the changes listed above |
| Protocol | identical flags to the paper's main tables; only the policy list differs |
| Anonymization | paths, user and host names in logs and result files were replaced by `$REPO`, `$HOME`, `user`, `node`; nothing else was edited |

Rows marked *paper* in the tables above are the printed values of the paper; rows marked *new* are computed from the files in this branch.

## Other rebuttal branches

Every branch starts from `main`, adds only what the reviewer asked for, and carries its own README.

| Branch | Reviewer | Points | Content |
|:---|:---:|:---:|:---|
| [`reviewer_5Xuf/W1_FactorRuleFilter`](../../tree/reviewer_5Xuf/W1_FactorRuleFilter) **(this branch)** | 5Xuf | W1, Q1 | `FactorRuleFilter` (sibling-propagation rule) under both main-table protocols, 36 run logs |
| [`reviewer_5Xuf/W2_exact`](../../tree/reviewer_5Xuf/W2_exact) | 5Xuf | W2, Q2 | exact period-by-period staleness update next to the first-order form used in the paper |
| [`reviewer_zpd8/Q1_failed_repair`](../../tree/reviewer_zpd8/Q1_failed_repair) | zpd8 | Q1 | what happens after a failed repair: reset (paper) vs. keep-as-broken-and-retry, 18 run logs |
| [`reviewer_zpd8/W1_two_counters`](../../tree/reviewer_zpd8/W1_two_counters) | zpd8 | W1, Q2 | two-counter policy under the MaskedMig main-table protocol, 9 run logs |
| [`reviewer_zpd8/W2_oracle_backoff`](../../tree/reviewer_zpd8/W2_oracle_backoff) | zpd8 | W2 | Oracle with back-off after a failed repair, 27 run logs |
| [`reviewer_TvTY/W1_release_signal`](../../tree/reviewer_TvTY/W1_release_signal) | TvTY | W1, Q1, Q2, Q3 | release-aware baseline (version bump / changelog), sensitivity sweep on the reviewer's grid, calibration data |
| [`reviewer_TvTY/W2_paired_tests`](../../tree/reviewer_TvTY/W2_paired_tests) | TvTY | W2, Q4, Q5, Q6 | paired McNemar tests for every main-table cell, low-quality-notes world, pointer to the Oracle ceiling |

The full code release and its documentation are on the [`main`](../../tree/main) branch. The data (19 generated worlds, 157 masked migrations) is in the companion [bayesian_harness_datasets](https://github.com/anonymouspaper987/bayesian_harness_datasets) repository.
