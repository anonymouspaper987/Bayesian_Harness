"""在留出的种子 4 上扫 ours_hhmm 的超参数 (离线模拟), 输出覆盖率/代理成功率. 不在种子 1-3 上调."""
import itertools, collections, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import offline_eval as oe
from bayesian_harness import filters as F
seeds = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "4").split(",")]; reps = int(sys.argv[2]) if len(sys.argv) > 2 else 4
grid = dict(hmm_rho=[0.3, 0.5, 0.7], hmm_recover=[0.5, 1.0, 2.0], hmm_bg=[0.006, 0.012, 0.024], pool_kappa=[2.0, 5.0, 10.0])
orig_build = F.build_filter
rows = []
def run_cfg(**kw):
    def bf(method, cfg=None, **_):
        c = F.FilterConfig(); [setattr(c, k, v) for k, v in kw.items()]; return F.HierarchicalHMMFilter(c)
    F.build_filter = bf; oe.build_filter = bf
    agg = collections.Counter()
    for s in seeds:
        for r in range(reps): agg.update(oe.run(s, "ours_hhmm", r))
    n = max(agg["n"], 1)
    return agg["cover"] / n * 100, agg["solved"] / n * 100, agg["probes"] / (reps * len(seeds)), agg["repair_ok"] / (reps * len(seeds))
# 基线: 现有 ours 和默认 hhmm
F.build_filter = orig_build; oe.build_filter = orig_build
for cond in ("ours", "ours_hhmm", "bayesian_agent"):
    agg = collections.Counter()
    for s in seeds:
        for r in range(reps): agg.update(oe.run(s, cond, r))
    n = max(agg["n"], 1); print(f"参照 {cond:<16} 覆盖 {agg['cover']/n*100:5.1f}  代理成功 {agg['solved']/n*100:5.1f}")
for combo in itertools.product(*grid.values()):
    kw = dict(zip(grid.keys(), combo)); cov, sol, pr, ok = run_cfg(**kw); rows.append((cov, sol, pr, ok, kw))
rows.sort(reverse=True)
print("\n前 8 组 (按覆盖率):")
for cov, sol, pr, ok, kw in rows[:8]: print(f"  覆盖 {cov:5.1f} 代理成功 {sol:5.1f} probes {pr:4.0f} 修好 {ok:3.0f}  {kw}")
print("默认组:", [r for r in rows if r[4] == dict(hmm_rho=0.5, hmm_recover=1.0, hmm_bg=0.012, pool_kappa=5.0)][0][:4])
covs = sorted(r[0] for r in rows); sols = sorted(r[1] for r in rows)
print(f"全部 {len(rows)} 组: 覆盖率 最低 {covs[0]:.1f} 中位 {covs[len(covs)//2]:.1f} 最高 {covs[-1]:.1f}; 代理成功 最低 {sols[0]:.1f} 中位 {sols[len(sols)//2]:.1f} 最高 {sols[-1]:.1f}")
print("最差 3 组:"); [print("  ", r[:2], r[4]) for r in rows[-3:]]
