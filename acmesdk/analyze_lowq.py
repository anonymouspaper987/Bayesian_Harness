"""低质量笔记设定 + validity-only 消融的分析 (真模型结果).
用法: python analyze_lowq.py --seed 1 --prefix qwen27b_mb24_lowq35 [--conds ours_hhmm,ours_hhmm_validity_only,bayesian_agent,ttl]
输出: 总体成功率; 自家笔记有效时的成功率, 按笔记是否低质量拆分 (= 质量 p_k 的经验值); 探测数、修复数、命中率 (repairs/probes)."""

import argparse, collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from sworld import seed_dir
ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--prefix", required=True)
ap.add_argument("--conds", default="ours_hhmm,ours_hhmm_validity_only,bayesian_agent,ttl")
a = ap.parse_args(); rd = f"{seed_dir(a.seed)}/results"
lq_path = f"{rd}/{a.prefix}.lowq.json"; LQ = set(json.load(open(lq_path))) if os.path.exists(lq_path) else set()
print(f"低质量笔记 {len(LQ)} 张 ({lq_path if LQ else '无'})")
print(f"{'cond':<26}{'n':>5}{'成功%':>7}{'有效|低质 成功%':>16}{'有效|正常 成功%':>16}{'无效 成功%':>11}{'probes':>8}{'repairs':>8}{'ok':>5}{'hit':>6}")
for c in a.conds.split(","):
    p = f"{rd}/{a.prefix}_{c}.jsonl"
    if not os.path.exists(p): print(f"{c:<26} (无结果)"); continue
    rows = [json.loads(l) for l in open(p)]
    def rate(rs): return f"{100*sum(r['solved'] for r in rs)/len(rs):.1f} (n={len(rs)})" if rs else "-"
    vlq = [r for r in rows if r.get("own_note_valid") and r["api_id"] in LQ]
    vhq = [r for r in rows if r.get("own_note_valid") and r["api_id"] not in LQ]
    inv = [r for r in rows if r.get("own_note_valid") is False]
    mp = f"{rd}/{a.prefix}_{c}.maint.jsonl"
    pr = rp = ok = 0
    if os.path.exists(mp):
        for m in map(json.loads, open(mp)): pr += m.get("probes", 0); rp += m.get("repairs", 0); ok += m.get("repair_ok", 0)
    print(f"{c:<26}{len(rows):>5}{100*sum(r['solved'] for r in rows)/len(rows):>7.1f}{rate(vlq):>16}{rate(vhq):>16}{rate(inv):>11}{pr:>8}{rp:>8}{ok:>5}{(rp/pr if pr else 0):>6.2f}")
