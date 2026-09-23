"""校准数据: staleness 后验的可靠性图 / Brier 分数 / 检测 precision-recall.
用 offline_eval 的无模型模拟器 (与附录 Table 6 同一工具), 在评测世界 seed 1/2/3 上, 每月维护前记录每张笔记的
过期信念 s 与真值 (笔记是否真的过期), 以及当月实际探测的笔记. 输出 JSON (可靠性曲线的原始点).
用法: python3 calib_eval.py [--seeds 1,2,3] [--reps 4] [--conds ours_hhmm,bayesian_agent,ttl] [--out ...]"""
import argparse, collections, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_eval as oe

def bins_of(pairs, nb=10):
    """等宽 10 桶: 每桶 (信念均值, 实际过期比例, 样本数)."""
    b = [[0.0, 0, 0] for _ in range(nb)]
    for s, y in pairs:
        i = min(int(s * nb), nb - 1); b[i][0] += s; b[i][1] += y; b[i][2] += 1
    return [(x[0] / x[2], x[1] / x[2], x[2]) if x[2] else (None, None, 0) for x in b]

def quantile_bins(pairs, nb=10):
    """等质量 10 桶: 桶边界取信念的分位数, 同一分数的样本落在同一桶 (不按真值拆开 tie), 空桶跳过.
    返回每桶 (信念均值, 实际过期比例, 样本数)."""
    import bisect
    ss = sorted(s for s, _ in pairs); n = len(ss)
    edges = sorted(set(ss[i * n // nb] for i in range(1, nb)))          # 重复边界合并 -> tie 不拆
    b = [[0.0, 0, 0] for _ in range(len(edges) + 1)]
    for s, y in pairs:
        i = bisect.bisect_right(edges, s); b[i][0] += s; b[i][1] += y; b[i][2] += 1
    return [(x[0] / x[2], x[1] / x[2], x[2]) for x in b if x[2]]

def pr_curve(pairs, npts=200):
    """按信念排序的 precision-recall 曲线与平均精度 AP, 同分块 (tie) 一起处理: 块内命中按块的平均率线性计入
    (等价于随机打散 tie 的期望), 曲线在块内也取样, 不依赖排序的偶然次序."""
    ps = sorted(pairs, key=lambda t: -t[0]); P = sum(y for _, y in ps); N = len(ps)
    marks = sorted(set([max(1, int(round(N * f / npts))) for f in range(1, npts + 1)] + [max(1, N // 2000), max(1, N // 500)]))
    curve = []; ap = 0.0; tp = 0.0; i = 0; mi = 0
    while i < N:
        j = i
        while j < N and ps[j][0] == ps[i][0]: j += 1
        blk = j - i; hits = sum(y for _, y in ps[i:j]); rate = hits / blk
        for q in range(1, blk + 1):
            ap += rate * ((tp + rate * q) / (i + q)) / max(P, 1)
        while mi < len(marks) and marks[mi] <= j:
            q = marks[mi] - i; tq = tp + rate * q
            curve.append((tq / max(P, 1), tq / marks[mi], ps[i][0])); mi += 1
        tp += hits; i = j
    return curve, ap

def prec_at_k(months, kmax=60):
    """每月候选按信念排序 (tie 内取期望), 前 k 张里真过期的比例, 再对所有月份平均."""
    acc = [0.0] * (kmax + 1); cnt = 0
    for cand in months:
        ps = sorted(cand, key=lambda t: -t[0]); N = len(ps)
        if N == 0: continue
        cnt += 1; exp_hits = 0.0; i = 0
        cum = [0.0] * (min(kmax, N) + 1)
        while i < N and i < kmax:
            j = i
            while j < N and ps[j][0] == ps[i][0]: j += 1
            rate = sum(y for _, y in ps[i:j]) / (j - i)
            for q in range(i + 1, min(j, kmax) + 1):
                cum[q] = cum[q - 1] + rate
            i = j
        for k in range(1, min(kmax, N) + 1): acc[k] += cum[k] / k
    return [acc[k] / max(cnt, 1) for k in range(1, kmax + 1)]

def decomposition(pairs, nb=10):
    """Brier = 可靠性 - 分辨率 + 不确定性 (Murphy 1973), 用分位数 10 桶."""
    n = len(pairs); base = sum(y for _, y in pairs) / n
    rel = res = 0.0
    for ms, fy, c in quantile_bins(pairs, nb):
        rel += c / n * (ms - fy) ** 2; res += c / n * (fy - base) ** 2
    return {"reliability": rel, "resolution": res, "uncertainty": base * (1 - base)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--conds", default="ours_hhmm,bayesian_agent,ttl"); ap.add_argument("--budget", type=float, default=24.0)
    ap.add_argument("--out", default="calibration_data.json")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]
    result = {"world_suffix": os.environ.get("ACME_WORLD_SUFFIX", ""), "seeds": seeds, "reps": a.reps, "budget": a.budget, "conds": {}}
    for cond in a.conds.split(","):
        rec = []; st = collections.Counter()
        for s in seeds:
            for r in range(a.reps): st.update(oe.run(s, cond, r, budget=a.budget, record=rec))
        pairs = []; probes = hits = stale_cands = 0; per_month = []; per_seed = collections.defaultdict(lambda: [0, 0, 0])
        months = []
        for mr in rec:
            kb = set(mr["known_broken"])
            cand = [(sc, y) for k, sc, y in mr["s"] if k not in kb]        # 已知坏掉待修的笔记不参与 (它们不需要再判断)
            pairs += cand; months.append(cand)
            p = len(mr["probed"]); h = sum(y for _, y in mr["probed"]); sc_ = sum(y for _, y in cand)
            probes += p; hits += h; stale_cands += sc_
            per_month.append((mr["seed"], mr["rep"], mr["m"], p, h, sc_))
            ps = per_seed[mr["seed"]]; ps[0] += p; ps[1] += h; ps[2] += sc_
        n = len(pairs); base = sum(y for _, y in pairs) / n
        brier = sum((sc - y) ** 2 for sc, y in pairs) / n
        eq = bins_of(pairs); ece = sum(c / n * abs(ms - fy) for ms, fy, c in eq if c)
        qb = quantile_bins(pairs); ece_q = sum(c / n * abs(ms - fy) for ms, fy, c in qb)
        curve, apr = pr_curve(pairs)
        result["conds"][cond] = {
            "n_pairs": n, "base_rate": base, "brier": brier, "brier_const": base * (1 - base), "ece": ece,
            "bins_equal": eq, "bins_quantile": qb, "ece_quantile": ece_q, "pr_curve": curve, "ap": apr,
            "decomposition": decomposition(pairs), "prec_at_k": prec_at_k(months),
            "probes": probes, "hits": hits, "stale_candidates": stale_cands,
            "precision": hits / max(probes, 1), "recall": hits / max(stale_cands, 1),
            "per_seed": {str(k): {"probes": v[0], "hits": v[1], "stale": v[2], "precision": v[1] / max(v[0], 1), "recall": v[1] / max(v[2], 1)} for k, v in per_seed.items()},
            "coverage": st["cover"] / max(st["n"], 1), "proxy_success": st["solved"] / max(st["n"], 1),
            "mean_probes_per_run": st["probes"] / (len(seeds) * a.reps),
        }
        c = result["conds"][cond]
        dc = c["decomposition"]; pk = c["prec_at_k"]
        print(f"   decomposition rel={dc['reliability']:.4f} res={dc['resolution']:.4f} unc={dc['uncertainty']:.4f}  prec@k k=1,5,10,12,24,48: " + " ".join(f"{pk[k-1]:.3f}" for k in (1,5,10,12,24,48)))
        print(f"{cond:<16} pairs={n:>7} base={base:.3f} Brier={brier:.4f} (const {c['brier_const']:.4f}) ECE={ece:.3f} ECEq={ece_q:.3f} AP={apr:.3f} "
              f"P={c['precision']:.3f} R={c['recall']:.3f} cover={c['coverage']:.3f} probes/run={c['mean_probes_per_run']:.0f}", flush=True)
    out = a.out if not a.tag else a.out.replace(".json", f"_{a.tag}.json")
    json.dump(result, open(out, "w"))
    print("wrote", out)

if __name__ == "__main__":
    main()
