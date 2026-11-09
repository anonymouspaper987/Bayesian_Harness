<h1 align="center">Reviewer zpd8 · Q1</h1>

<p align="center">
  <img alt="Reviewer: zpd8" src="https://img.shields.io/badge/Reviewer-zpd8-3C5488">
  <img alt="Points: Q1" src="https://img.shields.io/badge/Points-Q1-E64B35">
  <img alt="Switch: keep-stale-on-failed-repair" src="https://img.shields.io/badge/Switch-keep--stale--on--failed--repair-00A087">
  <img alt="Run logs: 18" src="https://img.shields.io/badge/Run_logs-18-8250DF">
  <img alt="Code: +1 flag" src="https://img.shields.io/badge/Code-+1_flag-0969DA">
</p>

> [!NOTE]
> **Branch for Reviewer zpd8, question 1.** It adds one switch to the AcmeSDK loop that keeps a note marked broken after
> a failed repair and retries it, and the 18 run logs of the comparison with the paper's behaviour (reset after any repair
> attempt), 3 models × 3 seeds × 2 variants.

## At a glance

| Reviewer's point | What this branch adds | Jump to |
|:---|:---|:---|
| **Q1** what happens to the note, its staleness belief and its quality counts after a failed repair; is retry suppression separate from the belief update | the answer in four steps, and an ablation that keeps the note as broken and retries it every period | [Response](#the-question) |

---

## The question

> **Q1:** After a failed repair, what happens to the note, its staleness belief, and its quality counts? Is retry suppression separate from the belief update?

**Response.** After a failed repair:

- **First**, the note stays the same. The agent still sees the old note in its exams.
- **Then**, we set its staleness to zero, so we stop treating it as broken for now, and the note goes to the end of the queue.
- **At the same time**, its quality counts stay the same. A failed fix does not tell us whether the note is good or bad.
- **Finally**, there is no separate rule that stops retries. If the note is still broken, it fails more exams, its staleness goes up again, and we try to fix it later. We will make this clear in the paper.

Based on the reviewer's suggestion, we ran the alternative: keep the note marked broken and retry it every period, since it keeps the highest score among all notes.

| (AcmeSDK, budget 12, 3 seeds) | Qwen3.5-4B | Qwen3.5-9B | Qwen3.5-27B |
|:---|:---:|:---:|:---:|
| **Reset after a failed repair (ours, paper behaviour)** | **`35.1 ± 4.1`** | **`46.6 ± 4.2`** | **`70.9 ± 2.2`** |
| Keep as broken and retry (new) | `25.9 ± 3.3` | `37.8 ± 3.1` | `68.5 ± 1.8` |

Keeping the note as broken and retrying it every period is worse at every size, because it spends the budget on repairs the model cannot make.

<p align="center"><img src="rebuttal/zpd8_Q1_failed_repair/q1_failed_repair.gif" alt="Reset vs keep-and-retry after a failed repair" width="900"></p>

## What changed in the code

| File | Change |
|:---|:---|
| [`acmesdk/loop.py`](acmesdk/loop.py) | New flag `--keep-stale-on-failed-repair` (default `0` = paper behaviour). With `1`, a failed repair is recorded as an invalid observation and the note goes back to the known-broken queue, so it is retried next month without a probe. |
| [`scripts/run_acmesdk_q1_failed_repair.sh`](scripts/run_acmesdk_q1_failed_repair.sh) | Runs both variants for one model and the given seeds. |

```python
if new is None and a.keep_stale_on_failed_repair:      # the repair did not pass verification
    flt.observe_probe(k, False, m)                      # count it as "seen broken"
    known_broken.add(k)                                 # retry next month
else:
    flt.on_repair(k, m)                                 # paper default: reset after any repair attempt
```

## What this branch adds

```text
rebuttal/zpd8_Q1_failed_repair/
  logs/acme_q{4b,9b,27b}_s{1,2,3}_mb12_reset.log            paper behaviour, one line per month
  logs/acme_q{4b,9b,27b}_s{1,2,3}_mb12_keep_and_retry.log   the alternative
  summary.csv                                              pooled pass rate ± s.d., written by make_gif.py from the logs
  make_gif.py                                              rebuilds summary.csv and the GIF
```

## Reproduce

```bash
scripts/run_acmesdk_q1_failed_repair.sh Qwen/Qwen3.5-4B "1 2 3"      # needs a model server and built worlds
python3 rebuttal/zpd8_Q1_failed_repair/make_gif.py                   # table and GIF from the logs
```

## Integrity

- The table above is read from `summary.csv`, which `make_gif.py` recomputes from the per-month lines of the 18 logs (pooled pass rate over three seeds, s.d. across seeds).
- Both variants of a seed ran against the same world copy and the same model server, in the same job.

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
| [`reviewer_zpd8/Q1_failed_repair`](../../tree/reviewer_zpd8/Q1_failed_repair) **(this branch)** | zpd8 | Q1 | what happens after a failed repair: reset (paper) vs. keep-as-broken-and-retry, 18 run logs |
| [`reviewer_zpd8/W1_two_counters`](../../tree/reviewer_zpd8/W1_two_counters) | zpd8 | W1, Q2 | two-counter policy under the MaskedMig main-table protocol, 9 run logs |
| [`reviewer_zpd8/W2_oracle_backoff`](../../tree/reviewer_zpd8/W2_oracle_backoff) | zpd8 | W2 | Oracle with back-off after a failed repair, 27 run logs |
| [`reviewer_TvTY/W1_release_signal`](../../tree/reviewer_TvTY/W1_release_signal) | TvTY | W1, Q1, Q2, Q3 | release-aware baseline (version bump / changelog), sensitivity sweep on the reviewer's grid, calibration data |
| [`reviewer_TvTY/W2_paired_tests`](../../tree/reviewer_TvTY/W2_paired_tests) | TvTY | W2, Q4, Q5, Q6 | paired McNemar tests for every main-table cell, low-quality-notes world, pointer to the Oracle ceiling |

The full code release and its documentation are on the [`main`](../../tree/main) branch. The data (19 generated worlds, 157 masked migrations) is in the companion [bayesian_harness_datasets](https://github.com/anonymouspaper987/bayesian_harness_datasets) repository.
