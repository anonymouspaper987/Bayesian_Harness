"""离线模拟: 新动作 "读某部门当月变更日志" (1 单位, 精确得知该部门当月变了哪些函数).
用法: python offline_cl.py --seeds 1,2,3 --reps 3
策略变体 (读日志的决定方式):
  none   不读 (现状)                     factor 因子模型: 部门 P(在大改) >= tau 或上月该部门有考试失败 -> 读
  all    每月读全部 12 个部门 (12 单位)   fail   只读上月有考试失败的部门 (不需要因子状态)"""
import argparse, collections, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sworld import seed_dir
from bayesian_harness.filters import FilterConfig, build_filter, HierarchicalHMMFilter
from offline_eval import load_world

def run(seed, cond, cl, rep, budget=24.0, repair_cost=3.0, floor=0.02, p_valid=0.9, p_invalid=0.42, p_repair=0.7, months=36, tau=0.2, max_reads=8):
    rng = random.Random(1000 * seed + rep)
    mba, exams, notes0 = load_world(seed)
    sd = seed_dir(seed)
    ev = json.load(open(f"{sd}/truth/events.json"))
    changed = collections.defaultdict(set)              # (sub, m) -> note ids changed that month
    for e in ev:
        changed[(e["sub"], e["m"])].add(e["id"])
        if e["type"] == "remove": changed[(e["sub"], e["m"])].add(e["detail"]["succ_id"])
    ids = sorted(n["note_id"] for n in notes0); sub = {n["note_id"]: n["sub"] for n in notes0}; subs = sorted(set(sub.values()))
    fresh_at = {k: 1 for k in ids}
    def valid(k, m): return not any(fresh_at[k] < em <= m for em in mba.get(k, ()))
    flt = build_filter(cond, FilterConfig())
    for k in ids:
        flt.add_factor("sub:" + sub[k], t=0); flt.add_entry(k, parents=("sub:" + sub[k],), t=0)
    use = collections.Counter(); st = collections.Counter(); ledger = []; known_broken = set(); failed_subs = set()
    for m in range(2, months + 1):
        pre = {k: valid(k, m) for k in ids}
        for solved, read in ledger:
            for nid in read: flt.update(nid, 1 if solved else 0, t=m, msg=None if solved else "env_changed", weight=1.0 / len(read))
        left = budget
        def repair(k):
            st["repairs"] += 1
            if rng.random() < p_repair: fresh_at[k] = m; st["repair_ok"] += 1
            flt.on_repair(k, m)
        # --- 新动作: 读变更日志 ---
        to_read = []
        if cl == "all": to_read = list(subs)
        elif cl == "fail": to_read = sorted(failed_subs)
        elif cl == "factor" and isinstance(flt, HierarchicalHMMFilter):
            for s_ in subs:
                f = "sub:" + s_
                b = flt._touch_factor(f, m)["b"] if f in flt.factors else 0.0
                if b >= tau or s_ in failed_subs: to_read.append((b, s_))
            to_read = [s_ for _, s_ in sorted(to_read, reverse=True)[:max_reads]]
        for s_ in to_read:
            if left < 1: break
            left -= 1; st["reads"] += 1
            hit = changed.get((s_, m), set())
            for k in ids:
                if sub[k] != s_: continue
                if k in hit:
                    st["read_hits"] += 1
                    flt.observe_probe(k, False, m)          # 日志说它变了 = 确认过时
                    known_broken.add(k)
                elif isinstance(flt, HierarchicalHMMFilter):
                    e = flt._touch_entry(k, m); e["s"] = e["s_pre"]   # 本月没变: 撤销本月的危险率增量
        # --- 修复已知坏的 ---
        for k in sorted(known_broken, key=lambda k: -use[k]):
            if left < repair_cost: break
            left -= repair_cost; known_broken.discard(k); repair(k)
        # --- 常规探测 ---
        psc = getattr(flt, "probe_score", None) or flt.stale_score
        for k in sorted(ids, key=lambda k: -psc(k, m) * (1.0 + use[k])):
            if left < 1 or k in known_broken: continue
            if flt.stale_score(k, m) < floor: break
            left -= 1; st["probes"] += 1
            flt.observe_probe(k, pre[k], m)
            if not pre[k]:
                st["hits"] += 1
                if left >= repair_cost: left -= repair_cost; repair(k)
                else: known_broken.add(k)
        ledger = []; failed_subs = set()
        for e in exams.get(m, []):
            own = e["api_id"]; v = valid(own, m)
            solved = rng.random() < (p_valid if v else p_invalid)
            sibs = sorted([k for k in ids if sub[k] == e["sub"] and k != own], key=lambda k: -use[k])[:2]
            read = [own] + sibs
            for nid in read: use[nid] += 1
            st["n"] += 1; st["solved"] += solved; st["cover"] += v
            if not solved: failed_subs.add(e["sub"])
            ledger.append((solved, read))
    return st

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args(); seeds = [int(x) for x in a.seeds.split(",")]
    variants = [("ours_hhmm", None), ("ours_hhmm", "factor"), ("ours_hhmm", "all"), ("ours_hhmm_no_factor", None), ("ours_hhmm_no_factor", "all"), ("ours_hhmm_no_factor", "fail"), ("bayesian_agent", None), ("bayesian_agent", "fail"), ("bayesian_agent", "all")]
    print(f"{'策略':<28}{'读日志':>8}{'代理成功':>9}{'覆盖率':>8}{'reads':>7}{'probes':>8}{'repairs(ok)':>13}{'读命中/读':>10}")
    for cond, cl in variants:
        agg = collections.Counter()
        for s in seeds:
            for r in range(a.reps): agg.update(run(s, cond, cl, r))
        n = max(agg["n"], 1); k = a.reps * len(seeds)
        print(f"{cond:<28}{str(cl):>8}{agg['solved']/n*100:>8.1f}%{agg['cover']/n*100:>7.1f}%{agg['reads']/k:>7.0f}{agg['probes']/k:>8.0f}{str(int(agg['repairs']/k))+' ('+str(int(agg['repair_ok']/k))+')':>13}{(agg['read_hits']/max(agg['reads'],1)):>10.2f}")

if __name__ == "__main__":
    main()
