"""受控环境里检验 validity / quality 两个变量是否需要分开 (不调模型, 单独可跑).
Note A: 有效时成功率 0.95, 在第 T_CHANGE 月过期 (之后成功率 = q = 0.1).  Note B: 成功率 0.60, 从不过期.
三种滤波器:  validity + quality = ours_hhmm;  validity-only = ours_hhmm_validity_only (p̄ 固定 5/6);
             quality-only = bayesian_agent (单个 Beta, 过期分 = 1 - 可靠度).
(a) 两条笔记的轨迹: 每月各考一次, 无探测, 看过期分 s 与质量估计 p̄ 随时间的变化 (500 次重复取平均).
(b) 群体版: 20 张高质量 (0.95) + 20 张中等质量 (0.60) 笔记, 同一子系统; 第 8 月起 6 张高质量笔记过期;
    每月每张考一次, 每月 3 次探测, 探到坏的立刻修好. 看修复延迟、白探花在哪类笔记上、覆盖率. (300 次重复)
用法: gemini_env/bin/python toy_quality.py"""
import collections, random, statistics, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from bayesian_harness.filters import FilterConfig, build_filter
Q = 0.10; VARIANTS = [("validity + quality (ours)", "ours_hhmm"), ("validity-only (p̄ fixed)", "ours_hhmm_validity_only"), ("quality-only (single Beta)", "bayesian_agent")]
def make(cond, ids):
    f = build_filter(cond, FilterConfig()); f.add_factor("sub:g", t=0)
    for k in ids:
        try: f.add_entry(k, parents=("sub:g",), t=0, weights={"sub:g": 1.0})
        except TypeError: f.add_entry(k, parents=("sub:g",), t=0)
    return f
def exam(f, k, p_true, valid, t, rng):
    y = 1 if rng.random() < (p_true if valid else Q) else 0
    f.update(k, y, t, msg=None if y else "env_changed", weight=1.0); return y
# ---------- (a) two notes ----------
T, TC, REPS = 20, 8, 500
print(f"(a) 两条笔记, A 在第 {TC} 月过期; 数值为 {REPS} 次重复的平均")
snap_months = [6, 8, 9, 10, 12, 16]
for lab, cond in VARIANTS:
    acc = collections.defaultdict(list)
    for rep in range(REPS):
        rng = random.Random(rep); f = make(cond, ["A", "B"])
        for t in range(1, T + 1):
            exam(f, "A", 0.95, t < TC, t, rng); exam(f, "B", 0.60, True, t, rng)
            if t in snap_months:
                acc[("sA", t)].append(f.stale_score("A", t)); acc[("sB", t)].append(f.stale_score("B", t))
                acc[("pA", t)].append(f.reliability("A")); acc[("pB", t)].append(f.reliability("B"))
    print(f"  {lab}")
    print("    月份        " + "".join(f"{m:>7}" for m in snap_months))
    for key, name in (("sA", "A 过期分 s"), ("sB", "B 过期分 s"), ("pA", "A 质量估计"), ("pB", "B 质量估计")):
        print(f"    {name:<10}" + "".join(f"{statistics.mean(acc[(key, m)]):7.2f}" for m in snap_months))
# ---------- (b) population with probes ----------
NH, NM, NCH, BUDGET, T2, REPS2 = 20, 20, 6, 3, 20, 300
print(f"\n(b) 群体: {NH} 张高质量(0.95) + {NM} 张中等质量(0.60), 第 {TC} 月起 {NCH} 张高质量笔记过期; 每月 {BUDGET} 次探测; {REPS2} 次重复")
print(f"{'滤波器':<28}{'修好过期笔记平均等了几个月':>26}{'白探: 中等质量笔记':>18}{'白探: 高质量笔记':>16}{'第8月后覆盖率':>14}")
for lab, cond in VARIANTS:
    delays, waste_m, waste_h, cover = [], 0, 0, []
    for rep in range(REPS2):
        rng = random.Random(10_000 + rep)
        ids = [f"H{i}" for i in range(NH)] + [f"M{i}" for i in range(NM)]
        p = {k: (0.95 if k[0] == "H" else 0.60) for k in ids}
        broken_at = {k: (TC if k in ids[:NCH] else None) for k in ids}     # 前 NCH 张高质量笔记在 TC 月过期
        valid = {k: True for k in ids}; f = make(cond, ids); fixed_at = {}
        for t in range(1, T2 + 1):
            for k in ids:
                if broken_at[k] == t: valid[k] = False
            # 维护: 按过期分探测 BUDGET 张
            for k in sorted(ids, key=lambda j: -f.stale_score(j, t))[:BUDGET]:
                f.observe_probe(k, valid[k], t)
                if valid[k]: waste_m += (k[0] == "M"); waste_h += (k[0] == "H")
                else: valid[k] = True; f.on_repair(k, t); fixed_at[k] = t
            # 考试: 每张一次
            for k in ids: exam(f, k, p[k], valid[k], t, rng)
            if t >= TC: cover.append(sum(valid.values()) / len(ids))
        for k in ids[:NCH]: delays.append((fixed_at.get(k, T2 + 1)) - TC)
    print(f"{lab:<28}{statistics.mean(delays):>26.1f}{waste_m/REPS2:>18.1f}{waste_h/REPS2:>16.1f}{100*statistics.mean(cover):>13.1f}%")
print("白探 = 探到的笔记其实没坏 (每次重复的平均次数); 覆盖率 = 第 8 月起每月有效笔记的比例.")
