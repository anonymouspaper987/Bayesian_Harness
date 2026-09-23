"""低质量笔记设定的离线模拟 (2026-09-18): 与 offline_eval.py 相同, 但每条笔记"有效时的成功率"可不同:
lowq_ids() 抽出的 API (--lowq-frac) 有效时只以 p_valid_low 的概率做对 (笔记写得差), 其余 p_valid.
用于比较 ours_hhmm (学习质量 p_k) 与 ours_hhmm_validity_only (p̄ 固定): 质量变量应当阻止低质量笔记的失败被误读为过期.
原说明:
用法: python offline_eval.py --seeds 1,2,3 --conds ours,ours_hhmm,ours_hhmm_nots,bayesian_agent,ttl,random --reps 5
指标: 代理成功率 (自家笔记有效时 0.9 做对, 否则 0.42), 考试时自家笔记有效的比例 (coverage), probes/repairs/hit."""
import argparse, collections, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sworld import seed_dir
from bayesian_harness.filters import FilterConfig, build_filter
from lowq import lowq_ids

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

def run(seed, cond, rep, budget=24.0, repair_cost=3.0, floor=0.02, p_valid=0.9, p_invalid=0.42, p_repair=0.7, months=36, adaptive=True, use_w=None, batch_cost=None, iface_cost=None, factors="sub", graph=None, propagate=None, prop_cost=1.0, p_prop=0.85, lowq_frac=0.0, p_valid_low=0.35, lowq_mode="random", fixed_pbar=None, value_rank=False):
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
    LQ = lowq_ids(seed, ids, lowq_frac, mode=lowq_mode, subs=sub)                 # 低质量笔记集合 (与 loop.py --lowq-frac 同一规则)
    fresh_at = {k: 1 for k in ids}                       # 笔记最后一次有效写入的月份
    def valid(k, m): return not any(fresh_at[k] < em <= m for em in mba.get(k, ()))
    flt = None
    if cond not in ("none", "static", "oracle"):
        _c = FilterConfig()
        if fixed_pbar is not None: _c.fixed_pbar = float(fixed_pbar)     # 固定 p̄ 扫描 (ours_hhmm_validity_only)
        flt = build_filter(cond, _c)
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
                def _val(j):
                    if not value_rank: return 1.0
                    return max(0.0, flt.reliability(j) - FilterConfig().q)   # 修好之后能多做对多少 = pbar - q
                def _key(j): return psc(j, m) * use_w(use[j]) * _val(j)
                cands = [k for k in ids if k not in known_broken]
                if not adaptive: cands.sort(key=lambda j: -_key(j))
                while left >= 1 and cands:
                    k = max(cands, key=_key) if adaptive else cands[0]
                    if flt.stale_score(k, m) < floor: break
                    cands.remove(k)
                    left -= 1; st["probes"] += 1
                    flt.observe_probe(k, pre[k], m)
                    if pre[k]: st["probe_valid"] += 1; st["probe_valid_lq"] += (k in LQ)   # 白探: 探到的是有效笔记
                    if not pre[k]:
                        st["hits"] += 1
                        if left >= rcost(k): left -= rcost(k); repair(k)
                        else: known_broken.add(k)
        ledger = []
        for e in exams.get(m, []):
            own = e["api_id"]; v = valid(own, m) if cond != "none" else False
            pv = p_valid_low if own in LQ else p_valid
            solved = rng.random() < (pv if v else p_invalid)
            st["n_lq"] += (own in LQ); st["cover_lq"] += (own in LQ) and v; st["cover_hq"] += (own not in LQ) and v
            sibs = sorted([k for k in ids if sub[k] == e["sub"] and k != own], key=lambda k: -use[k])[:2]
            read = ([own] if own in fresh_at else []) + sibs if cond != "none" else []
            for nid in read: use[nid] += 1
            st["n"] += 1; st["solved"] += solved; st["cover"] += v
            if read: ledger.append((solved, read))
    return st

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--conds", default="ours_hhmm,ours_hhmm_validity_only,bayesian_agent,ttl,random")
    ap.add_argument("--reps", type=int, default=5); ap.add_argument("--budget", type=float, default=24.0); ap.add_argument("--p-repair", type=float, default=0.7)
    ap.add_argument("--lowq-fracs", default="0,0.35"); ap.add_argument("--p-valid-low", type=float, default=0.35); ap.add_argument("--lowq-mode", default="random")
    ap.add_argument("--pbars", default="", help="逗号分隔: 额外跑 ours_hhmm_validity_only 在这些固定 p̄ 值上 (如 0.1,0.5,0.8333,0.95,1.0)")
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]
    for lf in [float(x) for x in a.lowq_fracs.split(",")]:
        print(f"\n=== lowq_frac={lf:g} mode={a.lowq_mode}  (低质量笔记有效时成功率 {a.p_valid_low:g}, 其余 0.9; {len(seeds)} seeds x {a.reps} reps, budget {a.budget:g}) ===")
        print(f"{'cond':<26}{'代理成功':>9}{'覆盖率':>8}{'覆盖:低质':>9}{'覆盖:正常':>9}{'probes':>8}{'白探':>7}{'白探:低质':>9}{'repairs':>9}{'hit':>6}")
        jobs = [(c, None) for c in a.conds.split(",")] + [("ours_hhmm_validity_only", float(x)) for x in a.pbars.split(",") if x]
        for cond, pb in jobs:
            agg = collections.Counter()
            for s_ in seeds:
                for r in range(a.reps):
                    agg.update(run(s_, cond, r, budget=a.budget, p_repair=a.p_repair, lowq_frac=lf, p_valid_low=a.p_valid_low, lowq_mode=a.lowq_mode, fixed_pbar=pb))
            cond = cond if pb is None else f"validity_only p̄={pb:g}"
            n = max(agg["n"], 1); pr = max(agg["probes"], 1); reps = a.reps * len(seeds)
            nlq = max(agg["n_lq"], 1); nhq = max(n - agg["n_lq"], 1)
            print(f"{cond:<26}{agg['solved']/n*100:>8.1f}%{agg['cover']/n*100:>7.1f}%{agg['cover_lq']/nlq*100:>8.1f}%{agg['cover_hq']/nhq*100:>8.1f}%"
                  f"{agg['probes']/reps:>8.0f}{agg['probe_valid']/reps:>7.0f}{agg['probe_valid_lq']/reps:>9.0f}{agg['repairs']/reps:>9.0f}{agg['hits']/pr:>6.2f}")

if __name__ == "__main__":
    main()
