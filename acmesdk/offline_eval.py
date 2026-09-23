"""不用模型的离线模拟: 用真值判断笔记有效性, 用简单概率代替模型做题/修复, 比较维护策略本身.
用法: python offline_eval.py --seeds 1,2,3 --conds ours,ours_hhmm,ours_hhmm_nots,bayesian_agent,ttl,random --reps 5
指标: 代理成功率 (自家笔记有效时 0.9 做对, 否则 0.42), 考试时自家笔记有效的比例 (coverage), probes/repairs/hit."""
import argparse, collections, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sworld import seed_dir
from bayesian_harness.filters import FilterConfig, build_filter

def load_world(seed):
    sd = seed_dir(seed)
    ev = json.load(open(f"{sd}/truth/events.json"))
    months_by_api = collections.defaultdict(set)
    for e in ev:
        months_by_api[e["id"]].add(e["m"])
        if e["type"] == "remove": months_by_api[e["detail"]["succ_id"]].add(e["m"])
    exams = collections.defaultdict(list)
    for e in map(json.loads, open(f"{sd}/exams.jsonl")): exams[e["m"]].append(e)
    notes = [json.loads(l) for l in open(f"{sd}/notes_M01.jsonl")]
    return months_by_api, exams, notes

def run(seed, cond, rep, budget=24.0, repair_cost=3.0, floor=0.02, p_valid=0.9, p_invalid=0.42, p_repair=0.7, months=36, adaptive=True, use_w=None, batch_cost=None, iface_cost=None, factors="sub", graph=None, propagate=None, prop_cost=1.0, p_prop=0.85, record=None):
    # propagate: None | "kernel" | "sub" | "llm": 修好一张后, 把修法套到同组、同一共因事件坏掉的兄弟上 (成本 prop_cost, 成功率 p_prop)
    # factors: sub | kernel | both | none | llm (graph = {note_id: [[factor, w], ...]})
    # iface_cost: 接口类变动 (改名/参数/删除) 的修复成本 (可按变更日志机械修复); None = 与其它相同
    # batch_cost: 同部门当月第 2 张起的修复成本 (None = 不打折). 批量修复: 变更日志共用.
    use_w = use_w or (lambda u: 1.0 + u)     # 探测排序里使用量的权重函数
    rng = random.Random(1000 * seed + rep)
    mba, exams, notes0 = load_world(seed)
    type_by_api = collections.defaultdict(list); cause_by_api = collections.defaultdict(list)
    apis_all = {a["id"]: a for a in json.load(open(f"{seed_dir(seed)}/truth/apis_M01.json"))}
    for e in json.load(open(f"{seed_dir(seed)}/truth/events.json")):
        type_by_api[e["id"]].append((e["m"], e["type"]))
        cause_by_api[e["id"]].append((e["m"], e.get("layer"), e.get("kernel"), e["sub"]))
        if e["type"] == "remove": type_by_api[e["detail"]["succ_id"]].append((e["m"], "remove"))
    ids = sorted(n["note_id"] for n in notes0); sub = {n["note_id"]: n["sub"] for n in notes0}
    fresh_at = {k: 1 for k in ids}                       # 笔记最后一次有效写入的月份
    def valid(k, m): return not any(fresh_at[k] < em <= m for em in mba.get(k, ()))
    flt = None
    if cond not in ("none", "static", "oracle"):
        flt = build_filter(cond, FilterConfig())
        apis = {a["id"]: a for a in json.load(open(f"{seed_dir(seed)}/truth/apis_M01.json"))}
        for k in ids:
            pas, w = [], {}
            if factors in ("sub", "both", "llm"): pas.append("sub:" + sub[k]); w["sub:" + sub[k]] = 1.0
            if factors in ("kernel", "both"): f = "kernel:" + apis[k]["kernel"]; pas.append(f); w[f] = 1.0
            if factors == "llm":
                for f, wt in (graph or {}).get(k, []):
                    if wt > 0: pas.append(f); w[f] = float(wt)
            if "tags" in cond: pas += ["tag:" + t for t in apis[k].get("tags", [])]; w.update({"tag:" + t: 1.0 for t in apis[k].get("tags", [])})
            for f in pas: flt.add_factor(f, t=0)
            try: flt.add_entry(k, parents=tuple(pas), t=0, weights=w)
            except TypeError: flt.add_entry(k, parents=tuple(pas), t=0)
    use = collections.Counter(); st = collections.Counter(); ledger = []; known_broken = set()
    for m in range(2, months + 1):
        pre = {k: valid(k, m) for k in ids}
        if cond not in ("none", "static"):
            if flt is not None:
                for solved, read in ledger:
                    for nid in read: flt.update(nid, 1 if solved else 0, t=m, msg=None if solved else "env_changed", weight=1.0 / len(read))
            if record is not None and flt is not None:      # 校准记录 (2026-09-18): 维护前每张笔记的过期信念与真值
                record.append({"seed": seed, "cond": cond, "rep": rep, "m": m, "known_broken": sorted(known_broken),
                               "s": [(k, float(flt.stale_score(k, m)), int(not pre[k])) for k in ids], "probed": []})
            left = budget
            repaired_subs = set()
            def rcost(k):
                if batch_cost is not None and sub[k] in repaired_subs: return batch_cost
                if iface_cost is not None:
                    ts = [t for em, t in type_by_api.get(k, []) if fresh_at[k] < em <= m]
                    if ts and all(t in ("rename", "param", "remove") for t in ts): return iface_cost
                return repair_cost
            def group_of(k):
                if propagate == "kernel": return frozenset({"kernel:" + apis_all[k]["kernel"]})
                if propagate == "sub": return frozenset({"sub:" + sub[k]})
                if propagate == "llm": g = (graph or {}).get(k, []); return frozenset({g[0][0]}) if g else frozenset()
                if propagate == "sub+llm":                       # 部门 或 LLM 家族 任一相同即视为同组
                    g = (graph or {}).get(k, []); return frozenset({"sub:" + sub[k]} | ({g[0][0]} if g else set()))
                return None
            def repair(k):
                nonlocal left
                st["repairs"] += 1; repaired_subs.add(sub[k])
                ok_ = rng.random() < p_repair
                if ok_: fresh_at[k] = m; st["repair_ok"] += 1
                if flt is not None: flt.on_repair(k, m)
                if ok_ and propagate:
                    # 这张笔记最近一次坏的原因 (月份, 层, 内核, 部门); 同组兄弟若因同一事件坏掉, 套用同一修法
                    causes = [c for c in cause_by_api.get(k, []) if c[0] <= m]
                    if not causes: return
                    cm, layer, kern, _ = causes[-1]
                    if layer not in ("kernel", "vertical", "horizontal"): return
                    g = group_of(k)
                    if not g: return
                    for j in ids:
                        if j == k or not (group_of(j) & g) or valid(j, m) or left < prop_cost: continue
                        cj = [c for c in cause_by_api.get(j, []) if c[0] <= m]
                        if not cj or cj[-1][0] != cm or cj[-1][1] != layer or cj[-1][2] != kern: continue
                        left -= prop_cost; st["prop"] += 1
                        if rng.random() < p_prop: fresh_at[j] = m; st["prop_ok"] += 1; known_broken.discard(j)
                        if flt is not None: flt.on_repair(j, m)
            for k in sorted(known_broken, key=lambda k: (sub[k] not in repaired_subs, -use[k])):
                if left < rcost(k): break
                left -= rcost(k); known_broken.discard(k); repair(k)
            if cond == "oracle":
                for k in sorted([k for k in ids if not pre[k]], key=lambda k: -use[k]):
                    if left < rcost(k): break
                    left -= rcost(k); repair(k)
            else:
                psc = getattr(flt, "probe_score", None) or flt.stale_score
                cands = [k for k in ids if k not in known_broken]
                if not adaptive: cands.sort(key=lambda j: -psc(j, m) * use_w(use[j]))
                while left >= 1 and cands:
                    k = max(cands, key=lambda j: psc(j, m) * use_w(use[j])) if adaptive else cands[0]
                    if flt.stale_score(k, m) < floor: break
                    cands.remove(k)
                    left -= 1; st["probes"] += 1
                    if record is not None and flt is not None: record[-1]["probed"].append((k, int(not pre[k])))
                    flt.observe_probe(k, pre[k], m)
                    if not pre[k]:
                        st["hits"] += 1
                        if left >= rcost(k): left -= rcost(k); repair(k)
                        else: known_broken.add(k)
        ledger = []
        for e in exams.get(m, []):
            own = e["api_id"]; v = valid(own, m) if cond != "none" else False
            solved = rng.random() < (p_valid if v else p_invalid)
            sibs = sorted([k for k in ids if sub[k] == e["sub"] and k != own], key=lambda k: -use[k])[:2]
            read = ([own] if own in fresh_at else []) + sibs if cond != "none" else []
            for nid in read: use[nid] += 1
            st["n"] += 1; st["solved"] += solved; st["cover"] += v
            if read: ledger.append((solved, read))
    return st

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--conds", default="ours,ours_hhmm,ours_hhmm_nots,bayesian_agent,ttl,random,oracle,static")
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--budget", type=float, default=24.0); ap.add_argument("--p-repair", type=float, default=0.7)
    a = ap.parse_args()
    print(f"{'cond':<16}{'代理成功':>9}{'覆盖率':>8}{'probes':>8}{'repairs':>9}{'ok':>6}{'hit':>6}")
    for cond in a.conds.split(","):
        agg = collections.Counter()
        for s in map(int, a.seeds.split(",")):
            for r in range(a.reps):
                agg.update(run(s, cond, r, budget=a.budget, p_repair=a.p_repair))
        n = max(agg["n"], 1); pr = max(agg["probes"], 1); reps = max(a.reps, 1) * len(a.seeds.split(","))
        print(f"{cond:<16}{agg['solved']/n*100:>8.1f}%{agg['cover']/n*100:>7.1f}%{agg['probes']/reps:>8.0f}{agg['repairs']/reps:>9.0f}{agg['repair_ok']/reps:>6.0f}{agg['hits']/pr:>6.2f}")

if __name__ == "__main__":
    main()
