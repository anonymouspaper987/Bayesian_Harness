"""Table 4 (tab:separability, "A poor note and a stale note are told apart") 的汇总.
结果文件命名约定:
  acmesdk : data/acmesdk/seed{S}/results/{tag}_mb{MB}_sep{MODE}_{pfx}{cond}.jsonl (+ .maint.jsonl)
  maskedmig: data/maskedmig/results/sep{MODE}_{tag}_s{S}_mb{MB}_{pfx}{cond}.jsonl
  pfx = "" | "pbar1_" (质量冻结) | "rw_" (低质量重写);  预检: mb48 的 oracle 文件, seed 1.
列: score on (poor,valid | good,stale) / quality on valid notes (cheap | full) / false alarms / caught / success renamed / all.
用法:
  python3 analyze_separability.py --corpus acme --tag qwen27b --mb 12 --mode keep --seeds 1,2,3 [--latex]
  python3 analyze_separability.py --precheck acme --tag qwen27b --modes keep,none,name [--json]
"""
import argparse, collections, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import strict_class                                     # noqa: E402
from analyze_robust import events                                    # noqa: E402

ROOT = os.path.dirname(HERE)
ACME = os.environ.get("ACMESDK_DATA") or os.path.join(ROOT, "data", "acmesdk")
MIG = os.environ.get("MASKEDMIG_DATA") or os.path.join(ROOT, "data", "maskedmig")
N_MIG = 471
POLICIES = [("Bayesian-Agent (single score)", "bayesian_agent", "", False),
            ("Two counters (TTL clock + success rate)", "two_counters", "", True),
            ("\\ours{}, quality frozen ($\\bar p_k{=}1$)", "ours_hhmm_validity_only", "pbar1_", False),
            ("\\ourrow \\textbf{\\ours{}}", "ours_hhmm", "", True),
            ("\\ours{} + rewrite on low quality", "ours_hhmm", "rw_", True)]


def path(corpus, tag, mb, mode, seed, pfx, cond):
    if corpus == "acme":
        return f"{ACME}/seed{seed}/results/{tag}_mb{mb}_sep{mode}_{pfx}{cond}.jsonl"
    return f"{MIG}/results/sep{mode}_{tag}_s{seed}_mb{mb}_{pfx}{cond}.jsonl"


def n_expected(corpus, seed):
    if corpus == "acme":
        return sum(1 for _ in open(f"{ACME}/seed{seed}/exams.jsonl"))
    return N_MIG


def load_run(corpus, f, seed):
    """一个 (策略, 种子) 文件 -> 原始累加量. 不满则返回 None."""
    if not os.path.exists(f):
        return None
    rows = [json.loads(l) for l in open(f)]
    if len(rows) < n_expected(corpus, seed) and not os.environ.get("SEP_ALLOW_PARTIAL"):
        return None
    mf = f[:-6] + ".maint.jsonl"
    agg = collections.Counter()
    g = {k: [0, 0.0, 0.0] for k in ("pv", "gs", "gv", "ps")}
    qv = {"cheap": [0, 0.0], "full": [0, 0.0]}
    months_pv_lt_gs = [0, 0]                    # 分组均值都有定义的月份里, poor-valid 均值 < good-stale 均值的次数 / 月数
    if os.path.exists(mf):
        for r in map(json.loads, open(mf)):
            for k in ("probes", "probe_valid", "breaks", "caught", "repairs", "repair_ok", "rewrites", "rewrite_ok"):
                agg[k] += r.get(k, 0) or 0
            grp = r.get("grp")
            if grp:
                for k, v in grp["score"].items():
                    g[k][0] += v[0]; g[k][1] += v[1]; g[k][2] += v[2]
                for k, v in grp["qual_valid"].items():
                    qv[k][0] += v[0]; qv[k][1] += v[1]
                pv, gs = grp["score"]["pv"], grp["score"]["gs"]
                if pv[0] and gs[0]:
                    months_pv_lt_gs[1] += 1
                    months_pv_lt_gs[0] += int(pv[1] / pv[0] < gs[1] / gs[0])
    al = [r["solved"] for r in rows]
    if corpus == "acme":
        ev = events(f"{ACME}/seed{seed}")
        rn = [r["solved"] for r in rows if strict_class(ev, r["api_id"], r["m"]) == "renamed"]
    else:
        rn = al                                  # MaskedMig 全部是删除类迁移 (renamed 层), renamed = all
    return {"agg": agg, "g": g, "qv": qv, "all": al, "ren": rn, "pv_lt_gs": months_pv_lt_gs}


def _mean_sd(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    return sum(vals) / len(vals), (statistics.stdev(vals) if len(vals) > 1 else None)


def summarize(runs):
    """runs: 每种子一个 load_run 结果 -> 各列 (合并值, 种子间 sd)."""
    out = {}
    def ratio(num, den):
        return 100.0 * num / den if den else None
    # 合并 (跨种子累加) 与每种子值
    tot = {k: [0, 0.0, 0.0] for k in ("pv", "gs")}
    totq = {"cheap": [0, 0.0], "full": [0, 0.0]}
    agg = collections.Counter(); pooled_all = []; pooled_ren = []
    per = collections.defaultdict(list)
    for r in runs:
        for k in tot:
            tot[k][0] += r["g"][k][0]; tot[k][1] += r["g"][k][1]
            per["score_" + k].append(r["g"][k][1] / r["g"][k][0] if r["g"][k][0] else None)
        for k in totq:
            totq[k][0] += r["qv"][k][0]; totq[k][1] += r["qv"][k][1]
            per["qual_" + k].append(r["qv"][k][1] / r["qv"][k][0] if r["qv"][k][0] else None)
        agg.update(r["agg"]); pooled_all += r["all"]; pooled_ren += r["ren"]
        per["fa"].append(ratio(r["agg"]["probe_valid"], r["agg"]["probes"]))
        per["caught"].append(ratio(r["agg"]["caught"], r["agg"]["breaks"]))
        per["all"].append(ratio(sum(r["all"]), len(r["all"])))
        per["ren"].append(ratio(sum(r["ren"]), len(r["ren"])))
    for k in tot:
        out["score_" + k] = (tot[k][1] / tot[k][0] if tot[k][0] else None, _mean_sd(per["score_" + k])[1], tot[k][0])
    for k in totq:
        out["qual_" + k] = (totq[k][1] / totq[k][0] if totq[k][0] else None, _mean_sd(per["qual_" + k])[1], totq[k][0])
    out["fa"] = (ratio(agg["probe_valid"], agg["probes"]), _mean_sd(per["fa"])[1], agg["probes"])
    out["caught"] = (ratio(agg["caught"], agg["breaks"]), _mean_sd(per["caught"])[1], agg["breaks"])
    out["ren"] = (ratio(sum(pooled_ren), len(pooled_ren)), _mean_sd(per["ren"])[1], len(pooled_ren))
    out["all"] = (ratio(sum(pooled_all), len(pooled_all)), _mean_sd(per["all"])[1], len(pooled_all))
    out["pv_lt_gs"] = [r["pv_lt_gs"] for r in runs]
    out["probes"] = agg["probes"]; out["repairs"] = agg["repairs"]; out["rewrites"] = agg["rewrites"]
    return out


def fmt(v, sd, nd, tex):
    if v is None:
        return "\\na" if tex else "-"
    s = f"{v:.{nd}f}"
    if sd is not None:
        s += (f"\\pmsd{{{sd:.{nd}f}}}" if tex else f"±{sd:.{nd}f}")
    return s


def block(corpus, tag, mb, mode, seeds, latex=False):
    title = {"acme": "\\sdk{}", "mig": "\\mig{}"}[corpus]
    print("=" * 100)
    print(f"{corpus} {tag} budget {mb} mode {mode} seeds {seeds}")
    print(f"{'policy':<44}{'poor,valid':>11}{'good,stale':>11}{'q.cheap':>9}{'q.full':>8}{'FA%':>8}{'caught%':>9}{'renamed':>9}{'all':>8}{'seeds':>6}")
    rows_tex = []
    for lab, cond, pfx, has_q in POLICIES:
        runs, used = [], []
        for s in seeds:
            r = load_run(corpus, path(corpus, tag, mb, mode, s, pfx, cond), s)
            if r is not None:
                runs.append(r); used.append(s)
        short = lab.replace("\\ourrow \\textbf{\\ours{}}", "Bayesian-Harness").replace("\\ours{}", "BH").replace("$\\bar p_k{=}1$", "p=1")
        if not runs:
            print(f"{short:<44}{'(no complete file)':>30}")
            rows_tex.append(None)
            continue
        o = summarize(runs)
        nd_score = 1 if cond == "two_counters" else 2           # 两计数器排序量是月龄, 其余是概率
        cells = [fmt(o["score_pv"][0], o["score_pv"][1], nd_score, latex), fmt(o["score_gs"][0], o["score_gs"][1], nd_score, latex)]
        if has_q:
            cells += [fmt(o["qual_cheap"][0], o["qual_cheap"][1], 2, latex), fmt(o["qual_full"][0], o["qual_full"][1], 2, latex)]
        else:
            cells += ["\\na" if latex else "n/a"] * 2
        cells += [fmt(o["fa"][0], o["fa"][1], 1, latex), fmt(o["caught"][0], o["caught"][1], 1, latex),
                  fmt(o["ren"][0], o["ren"][1], 1, latex), fmt(o["all"][0], o["all"][1], 1, latex)]
        plain = [c.replace("\\pmsd{", "±").replace("}", "").replace("\\na", "n/a") for c in cells]
        print(f"{short:<44}{plain[0]:>11}{plain[1]:>11}{plain[2]:>9}{plain[3]:>8}{plain[4]:>8}{plain[5]:>9}{plain[6]:>9}{plain[7]:>8}{''.join(map(str, used)):>6}"
              f"   probes {o['probes']} repairs {o['repairs']}" + (f" rewrites {o['rewrites']}" if pfx == "rw_" else "")
              + f"  n(pv,gs)={o['score_pv'][2]},{o['score_gs'][2]}  pv<gs months {o['pv_lt_gs']}")
        rows_tex.append(f"{lab} & " + " & ".join(cells) + "\\\\")
    if latex:
        print("-- LaTeX --")
        for r in rows_tex:
            print(r if r else "% (missing)")
    return rows_tex


def precheck(corpus, tag, modes, mb=48, seed=1, as_json=False):
    """预检: oracle + 便宜半区, 自家笔记有效时的考试通过率, 便宜修复 vs 完整修复. full 半区跨 mode 合并 (分布相同, 只为降噪)."""
    res = {}
    full_all = []
    per_mode = {}
    for mode in modes:
        f = path(corpus, tag, mb, mode, seed, "", "oracle")
        if not os.path.exists(f):
            per_mode[mode] = None; continue
        rows = [json.loads(l) for l in open(f)]
        cheap = [r["solved"] for r in rows if r.get("own_note_valid") and r.get("own_repair") == "cheap"]
        full = [r["solved"] for r in rows if r.get("own_note_valid") and r.get("own_repair") == "full"]
        per_mode[mode] = (cheap, full, len(rows))
        full_all += full
    pf_all = 100 * sum(full_all) / len(full_all) if full_all else None
    for mode, v in per_mode.items():
        if v is None:
            res[mode] = None; continue
        cheap, full, n = v
        pc = 100 * sum(cheap) / len(cheap) if cheap else None
        pf = 100 * sum(full) / len(full) if full else None
        res[mode] = {"n_rows": n, "n_cheap": len(cheap), "n_full": len(full), "cheap": pc, "full": pf,
                     "full_pooled": pf_all, "gap": (pf - pc) if (pc is not None and pf is not None) else None,
                     "gap_pooled": (pf_all - pc) if (pc is not None and pf_all is not None) else None}
    if as_json:
        print(json.dumps(res))
    else:
        print(f"预检 {corpus} {tag} mb{mb} seed{seed}: 自家笔记有效时的通过率, 便宜修复 vs 完整修复 (full 跨 mode 合并 {pf_all if pf_all is None else round(pf_all, 1)})")
        for mode, r in res.items():
            if r is None:
                print(f"  {mode:<5} (no file)"); continue
            print(f"  {mode:<5} rows {r['n_rows']:>4}  cheap {r['cheap'] if r['cheap'] is None else round(r['cheap'], 1)} (n={r['n_cheap']})"
                  f"  full {r['full'] if r['full'] is None else round(r['full'], 1)} (n={r['n_full']})  gap {r['gap'] if r['gap'] is None else round(r['gap'], 1)}"
                  f"  gap vs pooled full {r['gap_pooled'] if r['gap_pooled'] is None else round(r['gap_pooled'], 1)}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="acme", choices=["acme", "mig"])
    ap.add_argument("--tag", default="qwen27b")
    ap.add_argument("--mb", type=int, default=12)
    ap.add_argument("--mode", default="keep")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--precheck", default="", help="acme | mig: 预检模式")
    ap.add_argument("--modes", default="keep,none,name")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.precheck:
        precheck(a.precheck, a.tag, [m for m in a.modes.split(",") if m], as_json=a.json)
        return
    block(a.corpus, a.tag, a.mb, a.mode, [int(s) for s in a.seeds.split(",") if s], latex=a.latex)


if __name__ == "__main__":
    main()
