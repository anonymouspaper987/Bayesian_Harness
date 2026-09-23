"""固定 p̄ 扫描 + 低质量笔记设定的真模型结果汇总.
用法: python analyze_pbar.py --seed 1 --mb 12
扫 seed{S}/results/qwen27b_mb{MB}_*.jsonl 里前缀含 pbar / lowq 的运行以及基线 ours_hhmm, 打印:
成功率, 覆盖率 (考试时自家笔记有效), probes, 白探 (probe_valid, 探到有效笔记), repairs, 命中率 = 1 - 白探/probes."""

import argparse, glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from sworld import seed_dir
ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--mb", type=int, default=12)
a = ap.parse_args(); rd = f"{seed_dir(a.seed)}/results"
files = sorted(glob.glob(f"{rd}/qwen27b_mb{a.mb}_*.jsonl"))
rows = []
for p in files:
    if p.endswith(".maint.jsonl") or p.endswith(".strict.jsonl"): continue
    stem = os.path.basename(p)[:-6]
    if not (re.search(r"_pbar[\d.]+_|_lowq\d+", stem) or stem.endswith("_ours_hhmm") or stem.endswith("_ours_hhmm_validity_only")): continue
    rs = [json.loads(l) for l in open(p)]
    mp = p[:-6] + ".maint.jsonl"; pr = pv = rp = ok = 0
    if os.path.exists(mp):
        for m in map(json.loads, open(mp)): pr += m.get("probes", 0); pv += m.get("probe_valid", 0); rp += m.get("repairs", 0); ok += m.get("repair_ok", 0)
    cov = [r for r in rs if r.get("own_note_valid") is not None]
    rows.append((stem, len(rs), 100*sum(r["solved"] for r in rs)/len(rs), 100*sum(1 for r in cov if r["own_note_valid"])/max(len(cov),1), pr, pv, rp, ok))
print(f"{'run':<48}{'n':>5}{'成功%':>7}{'覆盖%':>7}{'probes':>8}{'白探':>6}{'repairs':>8}{'ok':>5}{'hit':>6}")
for stem, n, sr, cov, pr, pv, rp, ok in rows:
    print(f"{stem:<48}{n:>5}{sr:>7.1f}{cov:>7.1f}{pr:>8}{pv:>6}{rp:>8}{ok:>5}{(1-pv/pr if pr else 0):>6.2f}")
