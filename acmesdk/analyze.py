"""结果分析: 按漂移类型 / 首次vs重复遭遇 / 月份段拆解通过率, 并做条件间配对比较.

用法: python analyze.py --seed 1 --prefix calib_mb24 [--conds none,static,oracle]
"""

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sworld import seed_dir  # noqa: E402

NAME_TYPES = {"rename", "remove", "param"}      # 名字/签名变了: 旧笔记的名字不够用
BEHAV_TYPES = {"shape", "semantic"}             # 名字还在, 行为变了: 跑一下就能看出
STRICT_TYPES = {"rename", "remove"}             # 新名字无从猜起: 对任何模型都成立的地板


def drift_class(events_by_api, api_id, m):
    evs = [e for e in events_by_api.get(api_id, []) if e["m"] <= m]
    types = {e["type"] for e in evs}
    if types & NAME_TYPES:
        return "name"
    if types & BEHAV_TYPES:
        return "behavior"
    return "none"


def strict_class(events_by_api, api_id, m):
    evs = [e for e in events_by_api.get(api_id, []) if e["m"] <= m]
    types = {e["type"] for e in evs}
    if types & STRICT_TYPES:
        return "renamed"
    if "param" in types:
        return "param"
    return "behavior"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--conds", default="")
    a = ap.parse_args()
    sd = seed_dir(a.seed)
    events = json.load(open(f"{sd}/truth/events.json"))
    by_api = collections.defaultdict(list)
    for e in events:
        by_api[e["id"]].append(e)
        # 接班者继承前身的事件链 (删除事件挂在前身 id 上)
        if e["type"] == "remove":
            by_api[e["detail"]["succ_id"]].append(e)
    rdir = f"{sd}/results"
    have = sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []   # 刚 clone 的世界还没有 results 目录
    conds = [c for c in a.conds.split(",") if c] or \
        sorted({fn[len(a.prefix) + 1:-6] for fn in have
                if fn.startswith(a.prefix + "_") and fn.endswith(".jsonl")})
    if not conds:
        sys.exit(f"{rdir} 里没有前缀 {a.prefix} 的结果; 先跑 scripts/run_acmesdk.sh")
    R = {}
    for c in conds:
        p = f"{rdir}/{a.prefix}_{c}.jsonl"
        if os.path.exists(p):
            R[c] = {(r["task_id"], r["m"]): r for r in map(json.loads, open(p))}
    conds = [c for c in conds if c in R]
    if not conds:
        print("没有结果文件"); return

    def rate(rs):
        return f"{sum(r['solved'] for r in rs)/len(rs)*100:5.1f}% (n={len(rs)})" if rs else "   -   "

    print(f"{'条件':<15}{'总体':>16}{'名字变了':>18}{'只是行为变了':>18}{'首次遭遇':>16}{'重复遭遇':>16}")
    for c in conds:
        rs = list(R[c].values())
        cls = collections.defaultdict(list)
        for r in rs:
            cls[drift_class(by_api, r["api_id"], r["m"])].append(r)
        first = [r for r in rs if r["encounter"] == 0]
        rep = [r for r in rs if r["encounter"] > 0]
        print(f"{c:<15}{rate(rs):>16}{rate(cls['name']):>18}{rate(cls['behavior']):>18}"
              f"{rate(first):>16}{rate(rep):>16}")
    print(f"\n三分 (改名或删除 = 无从猜起 / 参数变了 = 可试 / 行为变了 = 可观察):")
    print(f"{'条件':<15}{'改名或删除':>18}{'参数变了':>18}{'行为变了':>18}")
    for c in conds:
        rs = list(R[c].values())
        cls = collections.defaultdict(list)
        for r in rs:
            cls[strict_class(by_api, r["api_id"], r["m"])].append(r)
        print(f"{c:<15}{rate(cls['renamed']):>18}{rate(cls['param']):>18}{rate(cls['behavior']):>18}")

    print("\n按月份段 (M02-12 / M13-24 / M25-36):")
    for c in conds:
        rs = list(R[c].values())
        segs = [[r for r in rs if lo <= r["m"] <= hi] for lo, hi in ((2, 12), (13, 24), (25, 36))]
        print(f"  {c:<14}" + "  ".join(rate(s) for s in segs))

    if len(conds) > 1:
        print("\n配对比较 (同一次考试, 赢/输):")
        base = conds[-1] if "ours" not in conds else "ours"
        for c in conds:
            if c == base:
                continue
            w = l = 0
            for k, r in R[base].items():
                o = R[c].get(k)
                if o is None:
                    continue
                w += r["solved"] and not o["solved"]
                l += o["solved"] and not r["solved"]
            print(f"  {base} vs {c}: 赢 {w} 输 {l}")

    print("\n失败原因 (每条件前 4):")
    for c in conds:
        errs = collections.Counter((r["err"] or "")[:40] for r in R[c].values() if not r["solved"])
        print(f"  {c}: {errs.most_common(4)}")


if __name__ == "__main__":
    main()
