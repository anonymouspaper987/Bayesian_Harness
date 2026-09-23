"""C1 命令行世界 (acmectl) 结果表: 与 Table 2 同格式, 27B 预算 24, 三种子合并 ± 种子间 sd, 列 = renamed / all.
    python3 make_cli_table.py [--seeds 1,2,3] [--max-month 36] [--latex]
--max-month: 只统计 m <= 该月的考试 (车道没跑完时, 取所有策略都到达的月份做公平对比)."""
import argparse, json, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from analyze import strict_class                                     # noqa: E402
from analyze_robust import events                                    # noqa: E402
ACME = os.environ.get("ACMESDK_DATA") or os.path.join(os.path.dirname(HERE), "data", "acmesdk")
ROWS = [("Oracle", "oracle"), ("None", "none"), ("Static", "static"), ("Random", "random"),
        ("Bayesian-Agent", "bayesian_agent"), ("TTL", "ttl"), ("\\ours{}", "ours_hhmm")]
ap = argparse.ArgumentParser(); ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--max-month", type=int, default=36)
ap.add_argument("--prefix", default="cli_qwen27b_mb24"); ap.add_argument("--latex", action="store_true"); a = ap.parse_args()
seeds = [int(s) for s in a.seeds.split(",")]
def last_month(cond, s):
    f = f"{ACME}/seed{s}/results/{a.prefix}_{cond}.jsonl"
    return max((json.loads(l)["m"] for l in open(f)), default=0) if os.path.exists(f) else 0
common = min(last_month(c, s) for _, c in ROWS for s in seeds)
M = min(a.max_month, common) if common else a.max_month
print(f"所有策略所有种子都到达的月份: M{common}; 本表统计 m <= {M}")
out = {}
for lab, cond in ROWS:
    pooled_r, pooled_a, per_r, per_a, n_ex = [], [], [], [], 0
    for s in seeds:
        f = f"{ACME}/seed{s}/results/{a.prefix}_{cond}.jsonl"
        if not os.path.exists(f): continue
        rows = [json.loads(l) for l in open(f)]; rows = [r for r in rows if r["m"] <= M]
        if not rows: continue
        ev = events(f"{ACME}/seed{s}")
        al = [r["solved"] for r in rows]; rn = [r["solved"] for r in rows if strict_class(ev, r["api_id"], r["m"]) == "renamed"]
        pooled_a += al; pooled_r += rn; per_a.append(100 * sum(al) / len(al)); per_r.append(100 * sum(rn) / len(rn) if rn else None)
    if not pooled_a: print(f"{lab:<16} (no file)"); continue
    mr = 100 * sum(pooled_r) / max(len(pooled_r), 1); ma = 100 * sum(pooled_a) / len(pooled_a)
    sr = statistics.stdev([x for x in per_r if x is not None]) if len([x for x in per_r if x is not None]) > 1 else None
    sa = statistics.stdev(per_a) if len(per_a) > 1 else None
    out[cond] = (mr, sr, ma, sa, len(pooled_r), len(pooled_a), len(per_a))
    f_ = lambda m, s: f"{m:.1f}" + (f"±{s:.1f}" if s is not None else "")
    print(f"{lab:<16} renamed {f_(mr, sr):>10} (n={len(pooled_r)})   all {f_(ma, sa):>10} (n={len(pooled_a)})   seeds {len(per_a)}")
if a.latex and out:
    c = lambda m, s: f"{m:.1f}" + (f"\\pmsd{{{s:.1f}}}" if s is not None else "")
    best_r = max((k for k in out if k not in ("oracle",)), key=lambda k: out[k][0]); best_a = max((k for k in out if k not in ("oracle",)), key=lambda k: out[k][2])
    print("-- LaTeX (Method & renamed & all) --")
    for lab, cond in ROWS:
        if cond not in out: continue
        mr, sr, ma, sa = out[cond][:4]
        r = c(mr, sr); al = c(ma, sa)
        if cond == best_r and cond != "oracle": r = f"\\best{{{mr:.1f}}}" + (f"\\pmsd{{{sr:.1f}}}" if sr is not None else "")
        if cond == best_a and cond != "oracle": al = f"\\best{{{ma:.1f}}}" + (f"\\pmsd{{{sa:.1f}}}" if sa is not None else "")
        row = f"{lab} & {r} & {al}\\\\"
        print(("\\ourrow \\textbf{\\ours{}}" + row[len("\\ours{}"):]) if cond == "ours_hhmm" else row)
    if "ours_hhmm" in out and "none" in out:
        print(f"% Delta vs None: renamed {out['ours_hhmm'][0]-out['none'][0]:+.1f}, all {out['ours_hhmm'][2]-out['none'][2]:+.1f}")
