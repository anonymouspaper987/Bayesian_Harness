"""以"世界 (seed)"而不是"考题"为独立单位的统计检验.
对每个 模型 x 预算 格子, 用三个 seed 的完整结果:
  (a) 逐世界配对差 (ours - baseline) 与胜负计数;
  (b) 世界层面的配对 t 检验 (n = 3 个世界) 与精确符号翻转检验;
  (c) 聚类 bootstrap 95% CI: 先有放回地重抽世界, 再在每个世界内重抽考题 (B 次).
用法: python world_level_stats.py [--mb 24] [--models qwen27b,main,g31pro] [--baselines ttl,bayesian_agent,random]"""
import argparse, collections, itertools, json, os, random, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from sworld import ROOT
ap = argparse.ArgumentParser()
ap.add_argument("--mbs", default="12,24,48"); ap.add_argument("--models", default="qwen4b,qwen9b,qwen27b,main,g31pro")
ap.add_argument("--baselines", default="ttl,bayesian_agent,random"); ap.add_argument("--B", type=int, default=4000); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args(); rng = random.Random(a.seed)
SEEDS = [s for s in (1, 2, 3) if os.path.exists(f"{ROOT}/seed{s}/exams.jsonl")]   # 没建的世界直接不算
NEX = {s: sum(1 for _ in open(f"{ROOT}/seed{s}/exams.jsonl")) for s in SEEDS}
NAMES = {"qwen4b": "Qwen3.5-4B", "qwen9b": "Qwen3.5-9B", "qwen27b": "Qwen3.5-27B", "main": "Gemini 2.5 Flash-Lite", "g31pro": "Gemini 3.1 Pro"}
def load(tag, mb, cond, s):
    p = f"{ROOT}/seed{s}/results/{tag}_mb{mb}_{cond}.jsonl"
    if not os.path.exists(p): return None
    rows = [json.loads(l) for l in open(p)]
    if s not in NEX or len(rows) < NEX[s]: return None
    return {(r["task_id"], r["m"]): int(r["solved"]) for r in rows}
def paired_t(diffs):
    n = len(diffs); m = statistics.mean(diffs); sd = statistics.stdev(diffs) if n > 1 else float("nan")
    if n < 2 or sd == 0: return m, float("nan"), float("nan")
    t = m / (sd / n ** 0.5)
    try:
        from scipy import stats; p = 2 * stats.t.sf(abs(t), n - 1)
    except Exception: p = float("nan")
    return m, t, p
def signflip_p(diffs):
    n = len(diffs); obs = sum(diffs); cnt = 0; tot = 0
    for signs in itertools.product((1, -1), repeat=n):
        tot += 1; cnt += abs(sum(s * d for s, d in zip(signs, diffs))) >= abs(obs) - 1e-12
    return cnt / tot
def cluster_boot(pairs, B):
    """pairs: list over worlds of (ours dict, base dict); returns 95% CI of pooled diff (pct points)."""
    worlds = list(range(len(pairs))); keys = [sorted(set(o) & set(b)) for o, b in pairs]
    diffs = []
    for _ in range(B):
        ws = [rng.choice(worlds) for _ in worlds]; num = 0.0; den = 0
        for w in ws:
            o, b = pairs[w]; ks = keys[w]; n = len(ks)
            for _ in range(n):
                k = ks[rng.randrange(n)]; num += o[k] - b[k]
            den += n
        diffs.append(100 * num / den)
    diffs.sort(); return diffs[int(0.025 * B)], diffs[int(0.975 * B)]
all_world_diffs = collections.defaultdict(list)   # baseline -> list of per-(config) world-mean diffs... we keep per world
per_world_by_base = collections.defaultdict(lambda: collections.defaultdict(list))  # base -> world -> list of config diffs
print(f"{'model':<22}{'budget':>7}{'baseline':>16}{'ours-base per world (s1,s2,s3)':>34}{'wins':>6}{'pooled':>8}{'cluster-boot 95% CI':>22}")
for mb in [int(x) for x in a.mbs.split(",")]:
    for tag in a.models.split(","):
        ours = {s: load(tag, mb, "ours_hhmm", s) for s in (1, 2, 3)}
        for base in a.baselines.split(","):
            bd = {s: load(tag, mb, base, s) for s in (1, 2, 3)}
            ws = [s for s in (1, 2, 3) if ours[s] and bd[s]]
            if len(ws) < 2: continue
            diffs = []
            for s in ws:
                ks = sorted(set(ours[s]) & set(bd[s]))
                diffs.append(100 * (sum(ours[s][k] for k in ks) - sum(bd[s][k] for k in ks)) / len(ks))
                per_world_by_base[base][s].append(diffs[-1])
            pooled_num = sum(sum(ours[s].values()) - sum(bd[s].values()) for s in ws); pooled_den = sum(len(ours[s]) for s in ws)
            lo, hi = cluster_boot([(ours[s], bd[s]) for s in ws], a.B)
            wins = sum(d > 0 for d in diffs)
            print(f"{NAMES[tag]:<22}{mb:>7}{base:>16}{' '.join(f'{d:+6.1f}' for d in diffs):>34}{f'{wins}/{len(ws)}':>6}{100*pooled_num/pooled_den:>+8.1f}{f'[{lo:+.1f}, {hi:+.1f}]':>22}")
print("\n世界层面汇总 (每个世界先对其所有 模型 x 预算 格子取平均差, 再以 3 个世界为独立单位检验):")
print(f"{'baseline':<16}{'mean diff per world (s1,s2,s3)':>34}{'paired t':>10}{'p (t)':>8}{'p (sign-flip, exact)':>22}")
for base, byw in per_world_by_base.items():
    wd = [statistics.mean(byw[s]) for s in sorted(byw)]
    m, t, p = paired_t(wd); ps = signflip_p(wd)
    print(f"{base:<16}{' '.join(f'{d:+6.1f}' for d in wd):>34}{t:>10.2f}{p:>8.3f}{ps:>22.3f}")
print("\n说明: 'wins' = ours 在几个世界里高于 baseline; pooled = 三世界合并的差 (百分点); cluster-boot = 先重抽世界再重抽考题的 95% 区间.")
print("符号翻转检验在 3 个世界时最小 p = 0.25, 因此第二张表主要看三世界的差是否同号、以及配对 t 的 p 值.")
