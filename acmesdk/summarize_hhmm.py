"""新方法 (ours_hhmm) 全套汇总: 主表三种子合并 + 配对检验, 预算/结构扫描, 部件消融, Flash-Lite 三种子, Pro.
维护列优先读 *_{cond}.maint.jsonl (新跑的都有), 否则从日志估. 用法: python summarize_hhmm.py"""

import json, collections, os, sys, glob, re
from math import comb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import strict_class
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.environ.get("ACMESDK_DATA") or os.path.join(_REPO, "data", "acmesdk")
L = os.environ.get("RUN_LOG_DIR") or os.path.join(_REPO, "logs")
COLS = ["renamed", "param", "behavior", "all", "first", "repeat"]
def p2(w, l):
    n = w + l; k = min(w, l); return min(1.0, sum(comb(n, i) for i in range(0, k + 1)) / 2 ** n * 2) if n else 1.0
def by_of(sd):
    ev = json.load(open(f"{sd}/truth/events.json")); by = collections.defaultdict(list)
    for e in ev:
        by[e["id"]].append(e)
        if e["type"] == "remove": by[e["detail"]["succ_id"]].append(e)
    return by
def load(sd, pfx, c):
    p = f"{sd}/results/{pfx}_{c}.jsonl"
    if not os.path.exists(p): return None
    rs = {(r["task_id"], r["m"]): r for r in map(json.loads, open(p))}
    n_ex = sum(1 for _ in open(f"{sd}/exams.jsonl"))
    return rs if len(rs) >= n_ex else None
def maint(sd, pfx, c):
    p = f"{sd}/results/{pfx}_{c}.maint.jsonl"
    if os.path.exists(p):
        ms = [json.loads(l) for l in open(p)]
        return sum(m["probes"] for m in ms), sum(m["repairs"] for m in ms), sum(m["repair_ok"] for m in ms)
    return None
def table(worlds, pfx, conds, title, ref="ours_hhmm"):
    """worlds: list of (sd) pooled. prints six-column rates per cond + paired vs ref."""
    agg = {c: collections.defaultdict(lambda: [0, 0]) for c in conds}; mt = {c: [0, 0, 0, False] for c in conds}
    pair = {c: [0, 0, 0, 0] for c in conds}   # w,l (all), w,l (renamed)
    missing = []
    for sd in worlds:
        if not os.path.exists(f"{sd}/truth/events.json"):     # 世界还没建, 整格算缺
            missing += [(os.path.basename(sd), c) for c in conds]
            continue
        by = by_of(sd); R = {}
        for c in conds:
            R[c] = load(sd, pfx, c)
            if R[c] is None: missing.append((os.path.basename(sd), c)); continue
            m = maint(sd, pfx, c)
            if m: mt[c][0] += m[0]; mt[c][1] += m[1]; mt[c][2] += m[2]; mt[c][3] = True
            for r in R[c].values():
                st = strict_class(by, r["api_id"], r["m"])
                for col in ("all", st, "first" if r["encounter"] == 0 else "repeat"):
                    agg[c][col][0] += int(bool(r["solved"])); agg[c][col][1] += 1
        if R.get(ref):
            for c in conds:
                if c == ref or not R.get(c): continue
                for k, r in R[ref].items():
                    o = R[c].get(k)
                    if o is None: continue
                    w = r["solved"] and not o["solved"]; l = o["solved"] and not r["solved"]
                    pair[c][0] += w; pair[c][1] += l
                    if strict_class(by, r["api_id"], r["m"]) == "renamed": pair[c][2] += w; pair[c][3] += l
    print(f"\n=== {title}  (n_all={agg[ref]['all'][1] if ref in agg else '-'})")
    if missing: print("  缺:", missing)
    print(f"{'cond':<26}" + "".join(f"{c:>9}" for c in COLS) + f"{'probes':>8}{'repairs(ok)':>13}{'hit':>6}   配对 vs {ref}: 全部 / 改名层")
    for c in conds:
        if agg[c]["all"][1] == 0: continue
        pc = lambda col: agg[c][col][0] / agg[c][col][1] * 100 if agg[c][col][1] else float('nan')
        pr, rp, ok, has = mt[c]; hit = f"{rp/pr:.2f}" if pr else "-"
        mcol = f"{pr:>8}{str(rp)+' ('+str(ok)+')':>13}{hit:>6}" if has else f"{'':>8}{'(无维护记录)':>13}{'':>6}"
        pp = "" if c == ref else f"   {pair[c][0]}/{pair[c][1]} p={p2(pair[c][0],pair[c][1]):.3f} / {pair[c][2]}/{pair[c][3]} p={p2(pair[c][2],pair[c][3]):.3f}"
        print(f"{c:<26}" + "".join(f"{pc(col):>9.1f}" for col in COLS) + mcol + pp)
ALL = ["oracle", "ours_hhmm", "ours", "ttl", "bayesian_agent", "random", "static", "none"]
table([f"{D}/seed{s}" for s in (1, 2, 3)], "qwen27b_mb24", ALL, "Qwen 27B 主世界 三种子合并 预算 24")
for s in (1, 2, 3): table([f"{D}/seed{s}"], "qwen27b_mb24", ["oracle", "ours_hhmm", "ours", "bayesian_agent", "ttl"], f"Qwen 27B 种子 {s}")
table([f"{D}/seed1"], "qwen27b_mb12", ALL, "Qwen 27B 种子 1 预算 12")
table([f"{D}/seed1"], "qwen27b_mb48", ALL, "Qwen 27B 种子 1 预算 48")
table([f"{D}/seed1_noburst"], "qwen27b_mb24", ALL, "Qwen 27B 种子 1 大改删掉 (匀着来)")
table([f"{D}/seed1_dense"], "qwen27b_mb24", ALL, "Qwen 27B 种子 1 大改加倍")
table([f"{D}/seed1"], "qwen27b_mb24", ["ours_hhmm", "ours_hhmm_no_factor", "ours_hhmm_no_pool", "ours_hhmm_no_recover", "ours_hhmm_no_error_channel", "ours", "bayesian_agent", "ttl"], "部件消融 种子 1 (ours = 上一版滤波器)")
for fc in ("0.1", "0.3", "0.5", "1.0"): table([f"{D}/seed1"], f"qwen27b_mb24_fc{fc}", ["ours_hhmm", "ours"], f"因子填错 {fc}")
table([f"{D}/seed{s}" for s in (1, 2, 3)], "main_mb24", ALL, "Flash-Lite 三种子合并")
table([f"{D}/seed1"], "g31pro_mb24", ALL, "Gemini 3.1 Pro 种子 1")

# ---- 表 2 补格 (09-07): 预算 12/48 的其他智能体, 都是种子 1 ----
for mb in (12, 48):
    table([f"{D}/seed1"], f"main_mb{mb}", ALL, f"Flash-Lite 种子 1 预算 {mb}")
    for m in ("qwen9b", "qwen4b"):
        table([f"{D}/seed1"], f"{m}_mb{mb}", ALL, f"{m} 种子 1 预算 {mb}")
    table([f"{D}/seed1"], f"g31pro_mb{mb}", ALL, f"Gemini 3.1 Pro 种子 1 预算 {mb}")
