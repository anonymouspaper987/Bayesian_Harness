> [!NOTE]
> **Branch for Reviewer zpd8, W2.** This branch adds a back-off switch for the Oracle and the run logs of the comparison. Everything else is the same as `main`.

## Reviewer zpd8, W2: failed repairs and the small-budget comparison with the Oracle

> **W2:** Sections 4.3 and 4.4 leave failed repairs unclear: repairs count only after passing an assertion, yet staleness resets after an attempt. If the old note remains unchanged, why reset its stale status? This also affects the small-budget comparison with the retrying oracle.


**Response:** Thank you for pointing this out; it was not made clear in the paper. And you are right about the small-budget comparison. Oracle retries every broken note in every period, even notes the model cannot fix. Our reset stops these retries. When we give Oracle the same kind of back-off (no retry for 3 months after a failed repair), it is clearly better than our method, as expected for an oracle.

> | (AcmeSDK, budget 12, 3 seeds) | Qwen3.5-4B | Qwen3.5-9B | Qwen3.5-27B |
> | :--- | :---: | :---: | :---: |
> | Oracle (retry every period) | `32.6 ± 4.2` | `40.4 ± 6.1` | `69.3 ± 3.0` |
> | Oracle with back-off | `46.1 ± 1.6` | `55.0 ± 4.5` | `73.8 ± 3.2` |
> | Bayesian-Harness (ours) | `35.1 ± 4.1` | `46.6 ± 4.2` | `70.9 ± 2.2` |

We will add Oracle with back-off mechanism to the paper and revise the claim. The comparisons with TTL, Bayesian-Agent and two counters are not affected, because these policies also reset a note after any repair attempt.

<p align="center"><img src="rebuttal/zpd8_W2_oracle_backoff/w2_oracle_backoff.gif" alt="Oracle with and without back-off vs. Bayesian-Harness" width="900"></p>

### Files

| Path | Content |
| :--- | :--- |
| [`acmesdk/loop.py`](acmesdk/loop.py) | New flag `--oracle-backoff N` (default `0` = paper behaviour). With `N > 0`, the Oracle does not retry a note for `N` months after a failed repair. |
| [`scripts/run_acmesdk_w2_oracle_backoff.sh`](scripts/run_acmesdk_w2_oracle_backoff.sh) | Runs Oracle, Oracle with back-off and Bayesian-Harness for one model. |
| [`rebuttal/zpd8_W2_oracle_backoff/logs/`](rebuttal/zpd8_W2_oracle_backoff/logs) | 27 run logs (3 models x 3 seeds x 3 policies). |
| [`rebuttal/zpd8_W2_oracle_backoff/make_gif.py`](rebuttal/zpd8_W2_oracle_backoff/make_gif.py) | Rebuilds the table ([`summary.csv`](rebuttal/zpd8_W2_oracle_backoff/summary.csv)) and the GIF from the logs. |

```bash
# reproduce one model (needs a model server, see scripts/serve_vllm.sh)
scripts/run_acmesdk_w2_oracle_backoff.sh Qwen/Qwen3.5-4B "1 2 3"
# rebuild the table and the GIF from the logs
python3 rebuttal/zpd8_W2_oracle_backoff/make_gif.py
```

The full code release and its documentation are on the [`main`](../../tree/main) branch.
