"""可分性的直接检验 (2026-09-18): 真实质量与真实过期 两两组合, 看模型估出来的两个量能不能各认各的.
四组笔记, 每组 10 张, 同一个子系统:
  高质量 不过期 (真实成功率 0.95)   高质量 第 8 月过期
  低质量 不过期 (真实成功率 0.60)   低质量 第 8 月过期
每月每张考一次, 不做探测 (探测会直接告诉模型答案, 那样检验不出推断能力).
可分 = 估计的质量只随真实质量变, 估计的过期只随真实过期变.
"""
import collections, random, statistics, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from bayesian_harness.filters import FilterConfig, build_filter
Q, N, TC, T, REPS = 0.10, 10, 8, 14, 200
GROUPS = [("高质量 不过期", 0.95, None), ("高质量 会过期", 0.95, TC), ("低质量 不过期", 0.60, None), ("低质量 会过期", 0.60, TC)]
def run(cond, kappa=None, months=(7, 12), probe_every=0):
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for rep in range(REPS):
        rng = random.Random(rep); cfg = FilterConfig()
        if kappa is not None: cfg.pool_kappa = kappa
        f = build_filter(cond, cfg); f.add_factor("sub:g", t=0)
        ids = []
        for gi, (lab, p, exp) in enumerate(GROUPS):
            for i in range(N):
                k = f"g{gi}_{i}"; ids.append((k, p, exp))
                try: f.add_entry(k, parents=("sub:g",), t=0, weights={"sub:g": 1.0})
                except TypeError: f.add_entry(k, parents=("sub:g",), t=0)
        for t in range(1, T + 1):
            if probe_every and t % probe_every == 0:      # 周期性探测: 直接读到真实有效性 (真实系统里探测就是这样)
                for k, p, exp in ids:
                    f.observe_probe(k, (exp is None) or (t < exp), t)
            for k, p, exp in ids:
                valid = (exp is None) or (t < exp)
                y = 1 if rng.random() < (p if valid else Q) else 0
                # 失败时的报错类型按论文标定的混淆矩阵抽 (笔记有效 -> 多半是 usage_error/transient; 已过期 -> 多半是 env_changed)
                msg = None
                if y == 0:
                    tbl = cfg.msg_probs_valid if valid else cfg.msg_probs_invalid
                    ks_, ws_ = list(tbl), [tbl[x] for x in tbl]
                    msg = rng.choices(ks_, weights=ws_)[0]
                f.update(k, y, t, msg=msg, weight=1.0)
            if t in months:
                for gi, (lab, p, exp) in enumerate(GROUPS):
                    ks = [f"g{gi}_{i}" for i in range(N)]
                    out[(lab, t)]["p"].append(statistics.mean(f.reliability(k) for k in ks))
                    out[(lab, t)]["s"].append(statistics.mean(f.stale_score(k, t) for k in ks))
    return out
import sys as _s
PE = int(_s.argv[1]) if len(_s.argv) > 1 else 0
for title, cond, kappa in ((f"我们的方法 (默认; 每 {PE} 个月探测一次)" if PE else "我们的方法 (默认, 无探测)", "ours_hhmm", None),
                           (f"我们的方法 (关掉借信息; 每 {PE} 个月探测一次)" if PE else "我们的方法 (关掉借信息)", "ours_hhmm", 0.0),
                           ("只有一个分数 (Bayesian-Agent)", "bayesian_agent", None)):
    o = run(cond, kappa, probe_every=PE)
    print(f"\n=== {title} ===")
    print(f"{'笔记类型':<18}{'真实质量':>9}{'第7月估的质量':>15}{'第12月估的质量':>16}{'第7月过期分':>13}{'第12月过期分':>14}")
    for lab, p, exp in GROUPS:
        print(f"{lab:<18}{p:>9.2f}{statistics.mean(o[(lab,7)]['p']):>15.2f}{statistics.mean(o[(lab,12)]['p']):>16.2f}"
              f"{statistics.mean(o[(lab,7)]['s']):>13.2f}{statistics.mean(o[(lab,12)]['s']):>14.2f}")
