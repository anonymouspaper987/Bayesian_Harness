"""用语法树级断言比对离线重判已有结果 (只影响 err=='assert modified' 的记录).

旧判分按行逐字比对断言, 会把"整段写成一行用分号连接"这类排版误判为改了断言.
重判规则与 loop.exam_one 一致: 断言语义保留 + 用了 acmesdk + 调用了当月现名 + 运行通过.
原文件备份为 <name>.strict.jsonl, 就地覆盖.

用法: python regrade_ast.py --seed 1 --prefix qwen27b_mb24 [--conds a,b] [--dry]
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.dirname(HERE))
sys.path.append(os.path.join(os.path.dirname(HERE), "maskedmig"))
from loop import World, asserts_preserved   # noqa: E402
from sworld import SynthEnv, seed_dir       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--conds", default="none,static,random,ttl,bayesian_agent,ours,oracle")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    W = World(a.seed, 36)
    rdir = f"{seed_dir(a.seed)}/results"
    for c in a.conds.split(","):
        p = f"{rdir}/{a.prefix}_{c}.jsonl"
        if not os.path.exists(p):
            continue
        recs = [json.loads(l) for l in open(p)]
        flipped = checked = 0
        for r in recs:
            if r["solved"] or r.get("err") != "assert modified":
                continue
            checked += 1
            t = W.tasks[r["task_id"]]
            sub = r["submission"]
            if not asserts_preserved(t["snippet"], sub) or "acmesdk" not in sub:
                r["err"] = "assert modified (ast)"
                continue
            cur = W.current_name(t["api_id"], r["m"])
            if cur and not re.search(rf"\b{re.escape(cur)}\b", sub):
                r["err"] = "current API not called"
                continue
            env = SynthEnv(a.seed, r["m"])
            try:
                ok, err = env.run_py(sub, timeout=60)
            finally:
                env.cleanup()
            if ok:
                r["solved"], r["err"] = True, ""
                flipped += 1
            else:
                r["err"] = (err or "")[:150]
        n_solved = sum(r["solved"] for r in recs)
        print(f"{a.prefix}_{c}: 复查 {checked} 条, 翻案 {flipped} 条 -> 通过率 {n_solved/len(recs)*100:.1f}%", flush=True)
        if not a.dry:
            bak = p.replace(".jsonl", ".strict.jsonl")
            if not os.path.exists(bak):
                shutil.copy(p, bak)
            with open(p, "w") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
