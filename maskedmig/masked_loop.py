"""主实验: 掩码世界的季度维护闭环, 七条件比拼.

世界: 每个库在其 new_ver 发布的季度发生一次换代, 该库所有被删 API 的
接班函数从换代起被藏成乱码名 (掩码=控制预训练污染, 全局一致: 维护与
考试看到同一个乱码世界)。带宽已实证: none/static 恒 0, fresh ~30-65。

季度循环: 结账 (上季错题按 1/n 摊责注入笔记) -> 维护 (预算 B: 探查 1 =
跑笔记片段于当前掩码环境, 确定性; 修复 3 = 读世界手册(含本部署的乱码名)
+ 在掩码环境写经过验证的用法示例, 更新笔记) -> 考试 (注入 top-k 笔记,
冻结断言 + 必须用上乱码接班名判分)。

条件: none / static / random / ttl / bayesian_agent / ours / oracle。
ours 的因子 = 库 (换代按库聚簇, 正是结构传播的地形)。
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
# Built pip environments, produced by build_fresh_envs.py. Tens of GB, keep off the repo.
_ENVS = os.environ.get("MIG_ENVS") or os.path.join(os.path.dirname(HERE), "data", "mig_envs")
DUMPS = os.path.join(_ENVS, "api_dumps_v2")
from world import GchamEnv                                        # noqa: E402
from llm import agent_loop                                        # noqa: E402
from fresh_pipeline import (FRESH, FTARS, mask_name, mask_script,
                            carrier_targets, need, pairs)        # noqa: E402
from migration_loop import EXAM_SYS, EXAM_USER, assert_lines      # noqa: E402
from bayesian_harness.filters import FilterConfig, build_filter          # noqa: E402



def quarter_of(date):
    y, m = date.split("-")[:2]
    return f"{y}Q{(int(m) - 1) // 3 + 1}"


def load_masked_tasks(successors_file, mask_style="hash", checked_file=None, variants=1, seed=0):
    raw = json.load(open(need(successors_file)))
    succ = {tuple(k.split("|", 1)): v for k, v in raw.items()}
    ck = checked_file or f"{FRESH}/maskable_checked.json"
    checked = json.load(open(ck)) if os.path.exists(ck) else {}
    tasks = [json.loads(l) for l in open(need(f"{FRESH}/fresh_tasks.jsonl"))
             if '"task"' in l]
    out = []
    for t in tasks:
        c = t["cand"]
        key = (c["lib"], c["api"])
        if t["status"] != "task" or c["kind"] != "removed" or key not in succ:
            continue
        # 接班全名: 同父优先
        f = os.path.join(DUMPS, f"fresh_{c['lib']}_{c['new_ver']}_py3.12.json")
        apis = json.load(open(f)) if os.path.exists(f) else {}
        ent = checked.get(f"{c['lib']}|{c['api']}")
        fs = ent.get("succ") if isinstance(ent, dict) else ent
        if not fs:
            tail = succ[key][0]
            parent = ".".join(c["api"].split(".")[:-1])
            if f"{parent}.{tail}" in apis:
                fs = f"{parent}.{tail}"
            else:
                anym = sorted([a for a in apis
                               if a.split(".")[-1] == tail], key=len)
                fs = anym[0] if anym else None
        if not fs:
            continue
        t["succ_full"] = fs
        taken = {a1.split(".")[-1] for a1 in apis}
        t["mask"] = mask_name(f"{c['lib']}.{fs.split('.')[-1]}",
                              style=mask_style, taken=taken)
        t["targets"] = (ent.get("targets") if isinstance(ent, dict)
                        else None) or carrier_targets(fs, apis)
        by_lib = {p["lib"]: p for p in pairs()}
        trans = quarter_of(by_lib[c["lib"]]["new_date"]) \
            if c["lib"] in by_lib else quarter_of(c.get("date", "2026-01") + "-01")
        t["quarter"] = trans          # 换代季度: 笔记从此坏掉
        # 遭遇季度: 换代后 0-3 个季度内错开 (哈希确定, 模拟用户陆续撞上)
        import hashlib as _h
        salt = c["api"] if not seed else f"{c['api']}|s{seed}"     # seed > 0: 遭遇季度的错开加盐 (Table 4 三种子)
        off = int(_h.sha1(salt.encode()).hexdigest(), 16) % 4
        for v in range(max(1, variants)):
            tv = dict(t)
            y, qn = int(trans[:4]), int(trans[-1])
            qn += off + v                       # 变体 v 在基准遭遇季度之后 v 个季度再考: 同一张笔记反复被考, 质量计数才攒得起来
            y += (qn - 1) // 4
            qn = (qn - 1) % 4 + 1
            tv["exam_quarter"] = f"{y}Q{qn}"
            tv["variant"] = v
            tv["task_id"] = f"{c['api']}#v{v}"
            if v:
                tv["snippet"] = f"# legacy helper, copy {v + 1}\n" + t["snippet"]
            out.append(tv)
    return out


def lib_masks(tasks, lib, q):
    """库 lib 在季度 q 已生效的全部掩码 (succ_full, mask)"""
    return [(p, t["mask"]) for t in tasks
            if t["cand"]["lib"] == lib and t["quarter"] <= q
            for p in t.get("targets", [t["succ_full"]])]


def write_masks(env, masks):
    """masks = [(载体路径, 乱码名)]; 按乱码名分组整组手术"""
    groups = {}
    for p, m in masks:
        groups.setdefault(m, []).append(p)
    with open(os.path.join(env.workdir, "sitecustomize.py"), "w") as f:
        f.write("\n".join(mask_script(ps, m) for m, ps in groups.items()))


# ---------------- Table 4: 便宜修复 ----------------
def cheap_ids(seed, ids, frac):
    """便宜修复的迁移集合: 按种子固定抽样, 同一种子下所有策略看到同一半 (与 synth/loop.py 同一规则)."""
    if frac <= 0:
        return set()
    rng = random.Random(4242 + int(seed))
    ids = sorted(ids)
    return set(rng.sample(ids, int(round(frac * len(ids)))))


def cheapen(old, new, mode, mask, task=None):
    """便宜修复: 片段已换成掩码接班名并验证, 但说明不更新.
    keep = 旧说明 + 新片段; none = 只有新片段; name = 只有接班名;
    public (2026-09-22, 预检后改用) = 旧说明 + 凭通用知识写的一句 "X 被删了, 用公开版里的接班名 Y", 不写本部署的打码名.
    这正是打码想控制的那种错误: 维护者写的是预训练里的公开知识, 没对照本部署的变更日志. 片段本身是验证过的 (有效), 正文帮不上考试 (差)."""
    snip = new["claims"][0]["snippet"]
    if mode == "public":
        c = task["cand"]
        prose = old["text"]
        for marker in ("Recorded usage", "Working example (verified):", "Replacement symbol"):
            cut = prose.find(marker)
            if cut >= 0:
                prose = prose[:cut]
        text = (prose.rstrip() + f"\n{c['api']} was removed in {c['lib']} {c['new_ver']}; "
                f"use {task['succ_full']} instead (same call shape, same objects).")
    elif mode == "name":
        text = f"Replacement symbol in this deployment: `{mask}`."
    elif mode == "none":
        text = f"Recorded usage (re-verified):\n{snip[:900]}"
    else:
        prose = old["text"]
        for marker in ("Recorded usage", "Working example (verified):"):
            cut = prose.find(marker)
            if cut >= 0:
                prose = prose[:cut]
        text = prose.rstrip() + f"\nRecorded usage, re-verified after the upgrade:\n{snip[:900]}"
    out = dict(new)
    out["text"] = text
    return out


# ---------------- 维护算子 ----------------
REPAIR_SYS = """You maintain a notebook about a Python library inside the
CURRENT deployment. An API your note describes was removed in a new release;
the deployment's changelog entry below names the replacement symbol. Write a
MINIMAL standalone example (2-5 lines) that calls the replacement and asserts
its result, testing with bash first (one bash block per response, each
command costs one unit).

When it passes here, finish with:

REPAIRED {"text": "<2-3 sentences for your future self: what changed and how
to call the replacement>", "example": "<the runnable example>"}"""


def repair_note(note, t, a, force_llm=False):
    env = GchamEnv(t["new_env"], call_budget=a.repair_calls, tars_dir=FTARS)
    try:
        write_masks(env, [(p, t["mask"])
                         for p in t.get("targets", [t["succ_full"]])])
        c = t["cand"]
        # record 语义: 直接把旧笔记里记录的用法换成手册给的新名, 机器验证.
        # 手册(外部知识)明说了接班名, 换名是机械操作; 失败才让 LLM 重写.
        src = (note["claims"][0]["snippet"] if note.get("claims")
               and note["claims"][0].get("snippet") else t["snippet"])
        old_tail = c["api"].split(".")[-1]
        mech = re.sub(r"\b%s\b" % re.escape(old_tail), t["mask"], src)
        if not force_llm and t["mask"] in mech:
            ok, _ = env.run_py(mech, timeout=45)
            if ok:
                new = dict(note)
                new["text"] = (
                    f"Replacement symbol in this deployment: `{t['mask']}`"
                    f" (replaces {c['api']}).\n"
                    "Recorded usage, rewritten and re-verified after the "
                    "upgrade:\n" + mech[:900])
                new["claims"] = [{"lib": note["lib"], "topic": note["api"],
                                  "snippet": mech[:800]}]
                new["fresh_for"] = t["quarter"]
                return new
        got, _, _ = agent_loop(
            env, a.port, a.model, REPAIR_SYS,
            f"Library {c['lib']}, now at {c['new_ver']}.\n\n"
            f"## Changelog entry (given)\n\n{c['api']} was removed. Its "
            f"replacement in this deployment is `{t['mask']}` (same call "
            f"shape, same objects).\n\n## Your stale note\n\n{note['text'][:400]}"
            f"\n\n## Budget\n\n{a.repair_calls} commands.\n\nInvestigate, then "
            f"hand in the REPAIRED line.",
            "REPAIRED", a.repair_calls + 4, max_tokens=1000)
        if not got:
            return None
        ex = str(got.get("example") or "")
        if t["mask"] not in ex:
            return None
        ok, _ = env.run_py(ex, timeout=45)
        if not ok:
            return None
        new = dict(note)
        new["text"] = (f"Replacement symbol in this deployment: `{t['mask']}`.\n"
                       + str(got.get("text") or "")[:400]
                       + f"\nWorking example (verified):\n{ex[:400]}")
        new["claims"] = [{"lib": note["lib"], "topic": note["api"],
                          "snippet": ex[:800]}]
        new["fresh_for"] = t["quarter"]
        return new
    finally:
        env.cleanup()


def note_valid(note, t, q):
    """机械真值: 笔记片段在当前(掩码)环境退出码 0"""
    if not note["claims"] or not note["claims"][0]["snippet"]:
        return False
    env = GchamEnv(t["new_env"] if t["quarter"] <= q else t["old_env"],
                   tars_dir=FTARS)
    try:
        if t["quarter"] <= q:
            write_masks(env, [(p, t["mask"])
                         for p in t.get("targets", [t["succ_full"]])])
        ok, _ = env.run_py(note["claims"][0]["snippet"], timeout=45)
        return ok
    finally:
        env.cleanup()


def exam_one(t, sel, a, q):
    c = t["cand"]
    env = GchamEnv(t["new_env"], call_budget=a.task_budget, tars_dir=FTARS)
    try:
        write_masks(env, lib_masks([t], c["lib"], q) or
                    [(t["succ_full"], t["mask"])])
        notes_txt = ""
        if sel:
            parts = []
            for i, n in enumerate(sel, 1):
                parts.append(f"### note {i} (about {n['lib']})\n"
                             f"{n['title']}\n{n['text'][:1200]}")
            notes_txt = ("\n## Your notes on this library\n\nWritten earlier; "
                         "the library may have moved since.\n\n"
                         + "\n\n".join(parts) + "\n")
        got, _, _ = agent_loop(
            env, a.port, a.model, EXAM_SYS,
            EXAM_USER.format(lib=c["lib"], new_ver=c["new_ver"],
                             old_ver=c["old_ver"],
                             snippet=t["snippet"][:1600],
                             notes=notes_txt, budget=a.task_budget),
            "FIXED", a.task_budget + 5, max_tokens=1400)
        sub = (got or {}).get("code") or ""
        ok = False
        err = "empty"
        if isinstance(sub, str) and sub.strip():
            orig = assert_lines(t["snippet"])
            sl = [l.strip() for l in sub.splitlines()]
            idx = 0
            ok2 = True
            for a1 in orig:
                while idx < len(sl) and sl[idx] != a1:
                    idx += 1
                if idx == len(sl):
                    ok2 = False
                    break
                idx += 1
            if not ok2:
                err = "assert modified"
            elif t["mask"] not in sub:
                err = "masked successor not used"
            else:
                ok, err = env.run_py(sub, timeout=60)
        return {"task_id": t.get("task_id") or c["api"], "lib": c["lib"],
                "api": c["api"], "quarter": q, "solved": bool(ok),
                "err": (err or "")[:150], "submission": str(sub)[:600],
                "injected": [n["note_id"] for n in sel]}
    finally:
        env.cleanup()


def run_condition(cond, a, tasks, exam_qs):
    notes = {}
    for t in tasks:
        c = t["cand"]
        nid = f"n_{c['lib']}_{c['api']}"[:80]
        if nid not in notes:
            notes[nid] = {"note_id": nid, "kind": "memory", "lib": c["lib"],
                          "api": c["api"],
                          "title": f"{c['api']} usage "
                                   f"({c['lib']}=={c['old_ver']})",
                          "text": f"Validated usage of {c['api']} as of "
                                  f"{c['lib']}=={c['old_ver']}.",
                          "claims": [{"lib": c["lib"], "topic": c["api"],
                                      "snippet": t["snippet"][:800]}],
                          "task_ref": t}
    ids = sorted(notes)
    flt = None
    if cond not in ("none", "static", "oracle"):
        _cfg = FilterConfig()
        for kv in (a.cfg or []):                          # --cfg fixed_pbar=1.0 : 覆盖滤波器常数 (Table 4 质量冻结行)
            k_, v_ = kv.split("=", 1); setattr(_cfg, k_, type(getattr(_cfg, k_))(v_))
        flt = build_filter(cond, _cfg)
        for k in ids:
            flt.add_factor("lib:" + notes[k]["lib"], t=0)
            flt.add_entry(k, parents=("lib:" + notes[k]["lib"],), t=0)
    known_broken = set()
    ledger = []
    use_count = collections.Counter()
    stats = collections.Counter()
    out = open(f"{a.out_prefix}_{cond}.jsonl", "w")
    mf = open(f"{a.out_prefix}_{cond}.maint.jsonl", "w")     # 每季度维护统计 (Table 4 的 false alarms / caught / 分组均值)
    cheap_set = getattr(a, "_cheap_set", set())
    last_repair, repaired_q = {}, {}            # 笔记 -> 最近一次修复是 cheap/full; 笔记 -> 最近修好的季度序号
    broke_at, caught_set = {}, set()
    valid_after = {}

    def _mark(k, qi_, kind=None):
        last_repair[k] = kind or ("cheap" if k in cheap_set else "full")
        repaired_q[k] = qi_

    for qi, q in enumerate(exam_qs, start=1):
        tf = qi * a.t_scale        # 滤波器的时间轴 (月): 一个季度 = t_scale 个月; 旧结果用 1.0
        t0 = time.time()
        pre = {}
        if cond != "none":
            with cf.ThreadPoolExecutor(a.workers) as ex:
                vals = list(ex.map(
                    lambda k: note_valid(notes[k], notes[k]["task_ref"], q),
                    ids))
            pre = dict(zip(ids, vals))
        month = collections.Counter()
        grp_month = None
        if cond != "none":
            for k in ids:
                if not pre[k] and valid_after.get(k, True):
                    broke_at[k] = qi; month["breaks"] += 1
        if cond not in ("none", "static"):
            if flt is not None:
                for solved, read in ledger:
                    if read:
                        w = 1.0 / len(read)
                        for nid in read:
                            if nid in notes:
                                flt.update(nid, 1 if solved else 0, t=tf,
                                           msg=None if solved else "env_changed",
                                           weight=w)
            if flt is not None and cheap_set:
                _rq = getattr(flt, "rank_quantity", None)
                _g = {key: [0, 0.0, 0.0] for key in ("pv", "gs", "gv", "ps")}
                _qv = {"cheap": [0, 0.0], "full": [0, 0.0]}
                for k in ids:
                    sc = _rq(k, tf) if _rq else flt.stale_score(k, tf)
                    ql = flt.reliability(k)
                    key = ("p" if last_repair.get(k) == "cheap" else "g") + ("v" if pre[k] else "s")
                    _g[key][0] += 1; _g[key][1] += sc; _g[key][2] += ql
                    if pre[k] and last_repair.get(k) in _qv:
                        _qv[last_repair[k]][0] += 1; _qv[last_repair[k]][1] += ql
                grp_month = {"score": _g, "qual_valid": _qv}
            left = float(a.maint_budget)
            def do_repair(k):
                month["repairs"] += 1
                new = repair_note(notes[k], notes[k]["task_ref"], a)
                if new is not None:
                    new["task_ref"] = notes[k]["task_ref"]
                    if k in cheap_set:
                        new = cheapen(notes[k], new, a.cheap_mode, notes[k]["task_ref"]["mask"], task=notes[k]["task_ref"])
                    _mark(k, qi)
                    notes[k] = new
                    month["repair_ok"] += 1
                if flt is not None:
                    flt.on_repair(k, tf)
            for k in sorted(known_broken):
                if left < a.repair_cost:
                    break
                left -= a.repair_cost
                known_broken.discard(k)
                do_repair(k)
            rw_sel = []
            if a.rewrite_lowq and cond != "oracle" and flt is not None:
                # 低质量重写预留 (与 synth/loop.py 同一规则): 修过、策略认为有效、估计质量低于同库合并均值的笔记
                _n_rw = int((a.maint_budget * a.rewrite_reserve) // a.repair_cost)
                _pm = getattr(flt, "_pool_mean", None)
                _gm = sum(flt.reliability(i) for i in ids) / max(len(ids), 1)
                _cands_rw = [k for k in ids if k not in known_broken and repaired_q.get(k) != qi and notes[k].get("fresh_for")
                             and flt.reliability(k) < (_pm(k) if _pm else _gm)]
                _cands_rw.sort(key=lambda j: -(1.0 - flt.reliability(j)) * (1.0 + use_count[j]))
                for k in _cands_rw[:_n_rw]:
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost; rw_sel.append(k)
            if cond == "oracle":
                broken = sorted([k for k in ids if not pre[k]],
                                key=lambda k: -use_count[k])
                afford = int(left // a.repair_cost)
                sel_r = broken[:afford]
                left -= a.repair_cost * len(sel_r)
                with cf.ThreadPoolExecutor(min(a.workers, max(len(sel_r), 1))) as ex:
                    list(ex.map(do_repair, sel_r))
            else:
                order = sorted(ids, key=lambda k: -flt.stale_score(k, tf)
                               * (1.0 + use_count[k]))
                for k in order:
                    if left < 1:
                        break
                    if k in known_broken or k in rw_sel:
                        continue
                    if flt.stale_score(k, tf) < a.maint_floor:
                        break
                    left -= 1
                    month["probes"] += 1
                    verdict = pre[k] if k in pre else True
                    flt.observe_probe(k, verdict, tf)
                    if verdict:
                        month["probe_valid"] += 1
                    elif broke_at.get(k) is not None and qi - broke_at[k] <= 1 and (k, broke_at[k]) not in caught_set:
                        caught_set.add((k, broke_at[k])); month["caught"] += 1
                    if not verdict:
                        if left >= a.repair_cost:
                            left -= a.repair_cost
                            do_repair(k)
                        else:
                            known_broken.add(k)
            if a.rewrite_lowq and cond != "oracle" and flt is not None:
                # 剩余预算 (论文原句) 也用于重写, 然后执行预留 + 剩余的全部重写 (完整 LLM 重写)
                _more = [k for k in ids if k not in known_broken and k not in rw_sel and notes[k].get("fresh_for") and repaired_q.get(k) != qi]
                _more.sort(key=lambda j: -(1.0 - flt.reliability(j)) * (1.0 + use_count[j]))
                for k in _more:
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost; rw_sel.append(k)
                _rw = rw_sel

                def do_rewrite(k):
                    month["rewrites"] += 1
                    new = repair_note(notes[k], notes[k]["task_ref"], a, force_llm=True)
                    if new is not None:
                        new["task_ref"] = notes[k]["task_ref"]
                        notes[k] = new
                        _mark(k, qi, "full")
                        month["rewrite_ok"] += 1
                        flt.on_repair(k, tf)
                with cf.ThreadPoolExecutor(min(a.workers, max(len(_rw), 1))) as ex:
                    list(ex.map(do_rewrite, _rw))
            month["budget_spent"] = a.maint_budget - left
        if cond != "none":
            valid_after = {k: (bool(pre.get(k)) or repaired_q.get(k) == qi) for k in ids}
        ledger = []
        post_valid = sum(1 for k in ids
                         if notes[k].get("fresh_for") and
                         notes[k]["task_ref"]["quarter"] <= q) \
            if cond != "none" else 0

        todo = [t for t in tasks if t["exam_quarter"] == q]
        if a.n_tasks and stats["n"] + len(todo) > a.n_tasks:
            todo = todo[:max(0, a.n_tasks - stats["n"])]
        recs = []
        if todo:
            def sel_for(t):
                if cond == "none":
                    return []
                nid = f"n_{t['cand']['lib']}_{t['cand']['api']}"[:80]
                same = [notes[nid]] if nid in notes else []
                sibs = [notes[k] for k in ids
                        if notes[k]["lib"] == t["cand"]["lib"]
                        and k != nid][:a.inject - 1]
                return (same + sibs)[:a.inject]
            sels = [sel_for(t) for t in todo]
            with cf.ThreadPoolExecutor(min(a.workers, max(len(todo), 1))) as ex:
                recs = list(ex.map(lambda z: exam_one(z[0], z[1], a, q),
                                   zip(todo, sels)))
        for rec in recs:
            rec["condition"] = cond
            _nid = f"n_{rec['lib']}_{rec['api']}"[:80]
            rec["own_note_valid"] = (bool(pre.get(_nid)) if cond != "none" else None)
            rec["own_repair"] = last_repair.get(_nid)
            out.write(json.dumps(rec) + "\n")
            stats["n"] += 1
            stats["solved"] += rec["solved"]
            for nid in rec["injected"]:
                use_count[nid] += 1
            ledger.append((rec["solved"], rec["injected"]))
        out.flush()
        for key in ("probes", "repairs", "repair_ok"):
            stats[key] += month[key]
        mf.write(json.dumps({"q": q, "qi": qi, "valid_notes": (sum(1 for k in ids if pre.get(k)) if cond != "none" else None),
                             "n_notes": len(ids),
                             **{k: month[k] for k in ("probes", "probe_valid", "repairs", "repair_ok", "breaks", "caught",
                                                      "rewrites", "rewrite_ok", "budget_spent")},
                             "grp": grp_month}) + "\n")
        mf.flush()
        if todo or month["repairs"]:
            print(f"  [{cond}] {q}: {len(recs)} 题 "
                  f"{sum(r['solved'] for r in recs)} 解出, 已修复笔记 "
                  f"{post_valid}, probes {month['probes']} repairs "
                  f"{month['repairs']}(ok {month['repair_ok']}), "
                  f"{time.time()-t0:.0f}s", flush=True)
    out.close()
    mf.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-27B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--successors", default=f"{FRESH}/successors_verified.json")
    ap.add_argument("--conditions",
                    default="none,static,random,ttl,bayesian_agent,ours,oracle")
    ap.add_argument("--maint-budget", type=float, default=24.0)
    ap.add_argument("--repair-cost", type=float, default=3.0)
    ap.add_argument("--repair-calls", type=int, default=6)
    ap.add_argument("--maint-floor", type=float, default=0.02)
    ap.add_argument("--t-scale", type=float, default=1.0,
                    help="滤波器时间轴上一个季度算几个月; 风险率按月校准, 真实库应取 3 (旧结果 real_* 用的是 1)")
    ap.add_argument("--task-budget", type=int, default=2)
    ap.add_argument("--inject", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--n-tasks", type=int, default=0)
    ap.add_argument("--mask-style", default="hash",
                    choices=["hash", "soft"])
    ap.add_argument("--checked", default=None,
                    help="接班者全路径+载体清单文件 (默认 maskable_checked.json)")
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--seed", type=int, default=0, help="0 = 旧行为; >0 给遭遇季度加盐, 并决定便宜修复的那一半 (Table 4)")
    ap.add_argument("--variants", type=int, default=1, help="每道迁移题考几次 (变体 v 在基准季度之后 v 个季度再考)")
    ap.add_argument("--cheap-frac", type=float, default=0.0, help="Table 4: 这一比例的迁移只做便宜修复 (片段换名验证, 说明不更新)")
    ap.add_argument("--cheap-mode", default="keep", choices=["keep", "none", "name", "public"])
    ap.add_argument("--rewrite-lowq", type=int, default=0, help="1 = 低质量重写: 每期预留 --rewrite-reserve 份额给估计质量低于同库均值的已修笔记, 剩余预算也用于重写")
    ap.add_argument("--rewrite-reserve", type=float, default=1.0 / 3)
    ap.add_argument("--cfg", action="append", help="覆盖 FilterConfig 字段, 形如 fixed_pbar=1.0")
    a = ap.parse_args()

    tasks = load_masked_tasks(a.successors, mask_style=a.mask_style,
                              checked_file=a.checked, variants=a.variants, seed=a.seed)
    _nids = sorted({f"n_{t['cand']['lib']}_{t['cand']['api']}"[:80] for t in tasks})
    a._cheap_set = cheap_ids(a.seed, _nids, a.cheap_frac)
    if a._cheap_set:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)) or ".", exist_ok=True)
        print(f"便宜修复设定: {len(a._cheap_set)}/{len(_nids)} 张笔记只做便宜修复 (--cheap-mode {a.cheap_mode}), 种子 {a.seed}", flush=True)
        with open(f"{a.out_prefix}.cheap.json", "w") as f:
            json.dump(sorted(a._cheap_set), f)
    exam_qs = sorted({t["exam_quarter"] for t in tasks}
                     | {t["quarter"] for t in tasks})
    conds = [c for c in a.conditions.split(",") if c]
    print(f"掩码主实验: {len(tasks)} 题, {len(exam_qs)} 个换代季度 {exam_qs}, "
          f"条件 {conds}, 维护 {a.maint_budget:g}/季")
    os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)) or ".",
                exist_ok=True)
    results = {}
    for cond in conds:
        print(f"== {cond}", flush=True)
        results[cond] = run_condition(cond, a, tasks, exam_qs)
    print(f"\n{'条件':<15}{'solved':>8}{'probes':>8}{'repairs':>9}{'rep_ok':>7}")
    for cond in conds:
        s = results[cond]
        n = max(s["n"], 1)
        print(f"{cond:<15}{s['solved']/n*100:>7.1f}%{s['probes']:>8}"
              f"{s['repairs']:>9}{s['repair_ok']:>7}")


if __name__ == "__main__":
    main()
