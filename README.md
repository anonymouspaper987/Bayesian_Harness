> [!NOTE]
> **Branch for Reviewer 5Xuf, W2 / Q2.** This branch adds the exact period-by-period update of Eq. 8 next to the first-order approximation used for the paper's numbers. Code only, no run logs.

## Reviewer 5Xuf, W2: approximation in `_touch_entry`

> **W2:** **Potential inconsistency between the paper and implementation.** Equation 7 and Appendix A.1 describe updating staleness period by period as the regime belief evolves. However, the released `_touch_entry` implementation appears to multiply the one-period hazard, evaluated using the final regime belief, by the number of elapsed periods.


**Response:**

Thank you for pointing it out. Your observation is correct since we made a first-order approximation in our implementation that affects MaskedMig benchmark only.

We therefore did the following **`four things`** to address this weakness:

1. **We ran the exact period-by-period update.** 

   We re-ran our method on MaskedMig with the update exactly as written in the paper, under the main-table protocol (`5` seeds):

   > | (MaskedMig, Qwen3.5-27B, budget 24, 5 seeds) | Accuracy (%) |
   > | :--- | :---: |
   > | Approximation (used in the paper) | `48.5 ± 2.2` |
   > | Exact period-by-period update | `46.9 ± 4.0` |
   > 
   > The two differ by only `1.6` points, well within the seed-to-seed variation (about `4` points), and make nearly the same maintenance decisions. Our conclusions are unchanged.

2. **We checked where the approximation affects.** 

   On AcmeSDK the filter is updated every month, so each update is exactly one period, which causes no effect on AcmeSDK. The approximation only affects MaskedMig, where each update spans one quarter (three months).

3. **we will revise our manuscript.** 

   We will revise `Eq. 8` and `Appendix A.1` to describe the approximation actually used, and add the comparison in the appendix.

4. **We will release both versions.** 

   The released code will include both the exact period-by-period update and first-order approximation.

> **Q2:** Is the multi-period update in `_touch_entry` an intentional approximation? How does it affect the results compared with the update described in the paper?


**Response:** "Your observation is correct. `_touch_entry` is a first-order approximation used to save computational cost. We conducted exact comparison experiments and reported the results in response to your W2. The effect is negligible, with only a 1.6-point difference within the variance (±4) and our conclusions described in the paper are unchanged.

<p align="center"><img src="rebuttal/W2_exact/w2_exact.gif" alt="Exact vs. first-order staleness update over a 3-month gap" width="950"></p>

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

The full code release and its documentation are on the [`main`](../../tree/main) branch.
