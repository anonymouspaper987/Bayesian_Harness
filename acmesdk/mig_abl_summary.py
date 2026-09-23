"""MaskedMig 部件消融 (三种子 x 471 题) 汇总: 每条件合并通过率 ± 种子间 sd, 逐种子相对全模型的差值, 以及按题配对的 W/L (不算 p).
    python3 mig_abl_summary.py [--prefix abl3_qwen27b] [--seeds 1,2,3]"""
import argparse, json, os, statistics
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIG = os.environ.get("MASKEDMIG_DATA") or os.path.join(_REPO, "data", "maskedmig")
CONDS = [("full model", "ours_hhmm"), ("no recovery", "ours_hhmm_no_recover"), ("no shared factor", "ours_hhmm_no_factor"),
         ("no pooling", "ours_hhmm_no_pool"), ("no error channel", "ours_hhmm_no_error_channel")]
ap = argparse.ArgumentParser(); ap.add_argument("--prefix", default="abl3_qwen27b"); ap.add_argument("--seeds", default="1,2,3"); a = ap.parse_args()
seeds = [int(s) for s in a.seeds.split(",")]
def load(cond, s):
    f = f"{MIG}/results/{a.prefix}_s{s}_mb24_{cond}.jsonl"
    if not os.path.exists(f): return None
    rows = [json.loads(l) for l in open(f)]
    return {(r["task_id"], r["quarter"]): bool(r["solved"]) for r in rows} if len(rows) >= 471 else None
full = {s: load("ours_hhmm", s) for s in seeds}
print(f"{'variant':<20}{'pooled %':>10}{'± sd':>7}{'per seed %':>26}{'vs full per seed':>26}{'W/L vs full':>14}")
for lab, cond in CONDS:
    per, deltas, W, L, n_all, n_ok = [], [], 0, 0, 0, 0
    for s in seeds:
        d = load(cond, s)
        if d is None: per.append(None); continue
        per.append(100 * sum(d.values()) / len(d)); n_all += len(d); n_ok += sum(d.values())
        if full[s]:
            deltas.append(per[-1] - 100 * sum(full[s].values()) / len(full[s]))
            W += sum(1 for k in d if d[k] and not full[s].get(k, False)); L += sum(1 for k in d if not d[k] and full[s].get(k, False))
    if not n_all: print(f"{lab:<20}{'(no complete file)':>17}"); continue
    vals = [p for p in per if p is not None]
    sd = f"{statistics.stdev(vals):.1f}" if len(vals) > 1 else "-"
    print(f"{lab:<20}{100*n_ok/n_all:>10.1f}{sd:>7}{'  '.join(f'{p:5.1f}' if p is not None else '  -  ' for p in per):>26}"
          f"{'  '.join(f'{d:+5.1f}' for d in deltas) if cond != 'ours_hhmm' else '':>26}{(f'{W}/{L}' if cond != 'ours_hhmm' else ''):>14}")
