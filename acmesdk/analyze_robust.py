"""附录 tab:robust (Model mismatch) 的汇总: 任意世界后缀 x 任意策略, 输出 renamed / all 通过率.
多种子时给合并通过率 + 种子间标准差. 用法:
    python3 analyze_robust.py --worlds fixed,htail,kern --seeds 1,2,3 --conds ours_hhmm,ttl,bayesian_agent,oracle
    python3 analyze_robust.py --worlds "" --seeds 1 --conds oracle        # 主世界 seed1 (后缀为空)
"""
import argparse, collections, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import strict_class                                     # noqa: E402

ACME = os.environ.get("ACMESDK_DATA") or os.path.join(os.path.dirname(HERE), "data", "acmesdk")
LABEL = {"ours_hhmm": "Bayesian-Harness", "ttl": "TTL", "bayesian_agent": "Bayesian-Agent",
         "oracle": "Oracle", "none": "None", "static": "Static", "random": "Random"}


def events(world):
    by = collections.defaultdict(list)
    for e in json.load(open(f"{world}/truth/events.json")):
        by[e["id"]].append(e)
        if e["type"] == "remove":
            by[e["detail"]["succ_id"]].append(e)
    return by


def cell(world_suffix, cond, seeds, mb=24, tag="qwen27b"):
    """返回 (renamed_mean, renamed_sd, all_mean, all_sd, used_seeds, missing_seeds)."""
    pooled_r, pooled_a, per_r, per_a, used, missing = [], [], [], [], [], []
    for s in seeds:
        w = f"{ACME}/seed{s}{world_suffix}"
        f = f"{w}/results/{tag}_mb{mb}_{cond}.jsonl"
        if not os.path.isdir(w) or not os.path.exists(f):
            missing.append(s)
            continue
        nex = sum(1 for _ in open(f"{w}/exams.jsonl"))
        rows = [json.loads(l) for l in open(f)]
        if len(rows) < nex:
            missing.append(s)
            continue
        ev = events(w)
        al = [r["solved"] for r in rows]
        rn = [r["solved"] for r in rows if strict_class(ev, r["api_id"], r["m"]) == "renamed"]
        pooled_a += al
        pooled_r += rn
        per_a.append(100 * sum(al) / len(al))
        per_r.append(100 * sum(rn) / len(rn))
        used.append(s)
    if not used:
        return None
    mr = 100 * sum(pooled_r) / len(pooled_r)
    ma = 100 * sum(pooled_a) / len(pooled_a)
    sr = statistics.stdev(per_r) if len(per_r) > 1 else None
    sa = statistics.stdev(per_a) if len(per_a) > 1 else None
    return mr, sr, ma, sa, used, missing


def tex(mr, sr, ma, sa):
    c = lambda m, s: (f"{m:.1f}\\pmsd{{{s:.1f}}}" if s is not None else f"{m:.1f}")
    return f"{c(mr, sr)} / {c(ma, sa)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", default="fixed,htail,kern", help="逗号分隔的世界后缀, 空串 = 主世界")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--conds", default="ours_hhmm,ttl,bayesian_agent,oracle")
    ap.add_argument("--mb", type=int, default=24)
    ap.add_argument("--tag", default="qwen27b", help="结果文件前缀, 与 run_acmesdk.sh 的 TAG 一致")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",") if s]
    conds = [c for c in a.conds.split(",") if c]
    for suf in a.worlds.split(","):
        ws = f"_{suf}" if suf else ""
        print("=" * 78)
        print(f"world seed*{ws or '(main)'}   Qwen3.5-27B  budget {a.mb}   cell = renamed / all")
        print(f"{'policy':<18}{'renamed':>14}{'all':>14}{'seeds':>8}   LaTeX")
        for cond in conds:
            r = cell(ws, cond, seeds, a.mb, a.tag)
            lab = LABEL.get(cond, cond)
            if r is None:
                print(f"{lab:<18}{'(no complete file)':>28}")
                continue
            mr, sr, ma, sa, used, missing = r
            f_ = lambda m, s: f"{m:.1f}" + (f" ±{s:.1f}" if s is not None else "")
            miss = f"  (missing seeds {missing})" if missing else ""
            print(f"{lab:<18}{f_(mr, sr):>14}{f_(ma, sa):>14}{''.join(map(str, used)):>8}   {tex(mr, sr, ma, sa)}{miss}")


if __name__ == "__main__":
    main()
