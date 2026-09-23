"""补充消融汇总 (真模型, 三 seed 合并 ± 各 seed 标准差): 改名类 / 全部 成功率, probes, 白探 (probe_valid), 命中率.
用法: python analyze_extra.py [--mb 24]   (扫 seed1-3/results 下列出的前缀)"""

import argparse, collections, json, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from analyze import strict_class
from sworld import ROOT
ap = argparse.ArgumentParser(); ap.add_argument("--mb", type=int, default=24); a = ap.parse_args()
ROWS = [("Bayesian-Harness (paper setting)", f"qwen27b_mb{a.mb}_ours_hhmm"),
        ("  probe ranking: staleness only", f"qwen27b_mb{a.mb}_rankstale_ours_hhmm"),
        ("  probe ranking: usage only", f"qwen27b_mb{a.mb}_rankuse_ours_hhmm"),
        ("  re-rank after every probe", f"qwen27b_mb{a.mb}_adapt_ours_hhmm"),
        ("  hazard rate lambda not learned", f"qwen27b_mb{a.mb}_ours_hhmm_fixed_lambda"),
        ("  30% subsystem labels wrong", f"qwen27b_mb{a.mb}_fc0.3_ours_hhmm"),
        ("  100% subsystem labels wrong", f"qwen27b_mb{a.mb}_fc1.0_ours_hhmm"),
        ("  no shared factor", f"qwen27b_mb{a.mb}_ours_hhmm_no_factor"),
        ("  validity-only (p̄ fixed 5/6)", f"qwen27b_mb{a.mb}_ours_hhmm_validity_only"),
        ("Bayesian-Agent", f"qwen27b_mb{a.mb}_bayesian_agent"), ("TTL", f"qwen27b_mb{a.mb}_ttl")]
def events(s):
    by = collections.defaultdict(list)
    for e in json.load(open(f"{ROOT}/seed{s}/truth/events.json")):
        by[e["id"]].append(e)
        if e["type"] == "remove": by[e["detail"]["succ_id"]].append(e)
    return by
SEEDS = [s for s in (1, 2, 3) if os.path.exists(f"{ROOT}/seed{s}/exams.jsonl")]   # 没建的世界直接不算
EV = {s: events(s) for s in SEEDS}; NEX = {s: sum(1 for _ in open(f"{ROOT}/seed{s}/exams.jsonl")) for s in SEEDS}
print(f"{'variant':<36}{'renamed':>14}{'all':>14}{'seeds':>7}{'probes':>8}{'白探':>6}{'hit':>6}")
for lab, stem in ROWS:
    pa, pr_, per_a, per_r, used = [], [], [], [], []; probes = pv = rp = 0; has_pv = False
    for s in (1, 2, 3):
        p = f"{ROOT}/seed{s}/results/{stem}.jsonl"
        if not os.path.exists(p): continue
        rows = [json.loads(l) for l in open(p)]
        if len(rows) < NEX[s]: continue
        al = [r["solved"] for r in rows]; rn = [r["solved"] for r in rows if strict_class(EV[s], r["api_id"], r["m"]) == "renamed"]
        pa += al; pr_ += rn; per_a.append(100 * sum(al) / len(al)); per_r.append(100 * sum(rn) / len(rn)); used.append(s)
        mp = p[:-6] + ".maint.jsonl"
        if os.path.exists(mp):
            for m in map(json.loads, open(mp)):
                probes += m.get("probes", 0); rp += m.get("repairs", 0)
                if "probe_valid" in m: pv += m["probe_valid"]; has_pv = True
    if not used: print(f"{lab:<36}{'(no complete run)':>28}"); continue
    sd = lambda v: f"±{statistics.stdev(v):.1f}" if len(v) > 1 else ""
    hit = (1 - pv / probes) if (has_pv and probes) else (rp / probes if probes else 0)
    print(f"{lab:<36}{100*sum(pr_)/len(pr_):>9.1f}{sd(per_r):<5}{100*sum(pa)/len(pa):>9.1f}{sd(per_a):<5}{''.join(map(str,used)):>7}{probes:>8}{(pv if has_pv else '-'):>6}{hit:>6.2f}")
print("hit = 1 - 白探/probes when probe_valid was logged (new runs), else repairs/probes.")
