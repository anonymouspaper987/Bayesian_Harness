"""T2' 微迁移考场: 冻结断言修复题 + 季度维护闭环.

考题: 一段在旧版环境验证通过的代码, 在当前(新版)环境必然失败; agent 在
新版环境里修好它, 但断言行一个字符都不许动。判分纯机械: (a) 提交里原
断言行按原顺序逐字保留, (b) 提交在新版环境退出码 0。无需标准答案,
无法靠删断言作弊。

笔记本: 每个 (库, API) 一条机械种子笔记, claim 片段即挖掘时验证过的旧
用法 (出生即真 100%, 出生环境=旧版)。static 下它随版本推进变错; 维护
(重生式修复)把它改写成新版行为, 而新版行为正是考题的答案所在。记忆
承重由构造保证。

时间线: 任务挂在其 new_ver 进入世界的季度; 维护/探查/修复/因子/预算
语义与 quarter_loop 完全一致 (共用 filters 与算子)。
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE)))
from world import load_world, GchamEnv                            # noqa: E402
from llm import agent_loop                                        # noqa: E402
from quarter_loop import rebirth_note, repair_note, note_valid    # noqa: E402
from bayesian_harness.filters import FilterConfig, build_filter          # noqa: E402

EXAM_SYS = """You maintain legacy code. A small snippet that worked on an
older version of a library now FAILS in the current environment. Repair it so
that the ORIGINAL assertions pass here.

This is a CUSTOMIZED DEPLOYMENT: some APIs here differ from the public
releases you know, including renamed symbols. When your notes state a
deployment-specific fact (like a replacement symbol), trust the note over
your general knowledge; verify with a command if unsure.

Example of the right instinct: a note says "foo.bar was removed; here its
replacement is `_r9x2ab`". The public replacement you remember is foo.baz,
but in THIS deployment `foo.baz` does not exist: `hasattr(foo, '_r9x2ab')`
returns True and `hasattr(foo, 'baz')` returns False. The fix that passes is
`foo._r9x2ab(...)`, not `foo.baz(...)`.

Hard rules:
- You must NOT modify, remove, or reorder any `assert` line: they are the
  spec. Change only the other lines (imports, setup, the calls).
- The repaired snippet must still USE this library to do the work: migrate
  the broken call to its current equivalent. Recomputing the values without
  the library counts as failure.
- Work it out with bash (`python -c "..."` or write and run a file), exactly
  ONE bash code block with ONE command per response, each command costs one
  unit of a small budget.

When your repaired snippet passes here (or budget is gone), finish with:

FIXED {"code": "<the full repaired snippet, original assert lines intact>"}"""

EXAM_USER = """Environment: {lib}=={new_ver} (this is the CURRENT version).

## The legacy snippet (worked on {lib}=={old_ver}, fails now)

```python
{snippet}
```
{notes}
## Budget

{budget} commands.

Repair it for THIS environment. The assert lines must remain exactly as they
are. Finish with the FIXED line."""


def render_notes(sel):
    if not sel:
        return ""
    parts = []
    for i, n in enumerate(sel, 1):
        parts.append(f"### note {i} (about {n['lib']})\n{n['title']}\n"
                     f"{n['text']}\nsnippet:\n{n['claims'][0]['snippet'][:400]}"
                     if n["claims"] else f"### note {i}\n{n['title']}\n{n['text']}")
    return ("\n## Your notes on these libraries\n\n"
            "Written earlier; the library may have moved since. Leads, not "
            "gospel.\n\n" + "\n\n".join(parts) + "\n")


def assert_lines(code):
    return [l.strip() for l in code.splitlines()
            if l.strip().startswith("assert")]


LIB_TOP = {"scikit-learn": "sklearn", "pillow": "PIL",
           "langchain-core": "langchain_core",
           "huggingface-hub": "huggingface_hub"}


def uses_library(submission, lib):
    """断言行以外的代码里必须有对该库的真实调用 (堵绕路)"""
    import re as _re
    top = LIB_TOP.get(lib, lib.replace("-", "_"))
    body_lines = [l for l in submission.splitlines()
                  if not l.strip().startswith("assert")]
    body = "\n".join(body_lines)
    m = _re.search(r"import\s+" + _re.escape(top) + r"(?:\s+as\s+(\w+))?",
                   body)
    alias = (m.group(1) if m and m.group(1) else top) if m else top
    froms = _re.findall(r"from\s+" + _re.escape(top) +
                        r"[\w.]*\s+import\s+([\w, ]+)", body)
    names = [alias] + [n.strip() for grp in froms for n in grp.split(",")]
    return any(_re.search(r"\b" + _re.escape(n) + r"\b\s*[.(]", body)
               for n in names if n)


def grade(task, submission, env):
    if not isinstance(submission, str) or not submission.strip():
        return False, "empty"
    if not uses_library(submission, task["cand"]["lib"]):
        return False, "bypass: library not used"
    orig = assert_lines(task["snippet"])
    sub_lines = [l.strip() for l in submission.splitlines()]
    idx = 0
    for a in orig:
        while idx < len(sub_lines) and sub_lines[idx] != a:
            idx += 1
        if idx == len(sub_lines):
            return False, "assert line missing or modified"
        idx += 1
    ok, err = env.run_py(submission, timeout=60)
    return ok, ("" if ok else err[:200])


def build_notebook(tasks, probs):
    """机械种子笔记: 每 (lib, api) 一条, claim=挖掘验证过的旧用法片段"""
    first_q = {}
    for p in probs:
        k = (p["library"], p["version"])
        q = p["quarter"]
        if k not in first_q or q < first_q[k]:
            first_q[k] = q
    notes = {}
    for t in tasks:
        c = t["cand"]
        key = (c["lib"], c["api"])
        if key in notes:
            continue
        born = first_q.get((c["lib"], c["old_ver"]), "2019Q1")
        notes[key] = {
            "note_id": f"mig_{c['lib']}_{c['api']}".replace("/", "_")[:80],
            "kind": "memory", "lib": c["lib"], "api": c["api"],
            "title": f"{c['api']} on {c['lib']}=={c['old_ver']}",
            "text": f"Validated usage of {c['api']} as of "
                    f"{c['lib']}=={c['old_ver']}; see snippet.",
            "claims": [{"lib": c["lib"], "topic": c["api"],
                        "snippet": t["snippet"][:1200]}],
            "born_quarter": born}
    return list(notes.values())


def retrieve(notes, task, k=4):
    api = task["cand"]["api"]
    lib = task["cand"]["lib"]
    scored = []
    for n in notes:
        s = 0.0
        if n["lib"] == lib:
            s += 1.0
        if n.get("api") == api:
            s += 2.0
        elif n.get("api", "").split(".")[-1] == api.split(".")[-1]:
            s += 1.0
        scored.append((s, n))
    scored.sort(key=lambda x: -x[0])
    return [n for s, n in scored[:k] if s > 0]


def exam_one(task, sel, a):
    c = task["cand"]
    env = GchamEnv(task["new_env"], call_budget=a.task_budget)
    try:
        got, steps, _ = agent_loop(
            env, a.port, a.model, EXAM_SYS,
            EXAM_USER.format(lib=c["lib"], new_ver=c["new_ver"],
                             old_ver=c["old_ver"],
                             snippet=task["snippet"][:1600],
                             notes=render_notes(sel),
                             budget=a.task_budget),
            "FIXED", a.task_budget + 5, max_tokens=1400)
        sub = (got or {}).get("code") or ""
        ok, err = grade(task, sub, env)
        return {"task_id": task["task_id"], "lib": c["lib"],
                "api": c["api"], "quarter": task["quarter"],
                "solved": ok, "err": err, "submission": sub[:800],
                "tool_calls": env.n_calls,
                "steps": steps, "injected": [n["note_id"] for n in sel]}
    finally:
        env.cleanup()


def run_condition(cond, a, tasks, notes0, probs, exam_qs):
    notes = {n["note_id"]: json.loads(json.dumps(n)) for n in notes0}
    flt = None
    if cond not in ("none", "static", "oracle"):
        flt = build_filter(cond, FilterConfig())
    registered = set()
    known_broken = set()
    ledger = []
    stats = collections.Counter()
    out = open(f"{a.out_prefix}_{cond}.jsonl", "w")

    for qi, q in enumerate(exam_qs, start=1):
        t0 = time.time()
        ids = sorted(k for k, v in notes.items()
                     if v["born_quarter"] <= q)
        if flt is not None:
            for k in ids:
                if k not in registered:
                    flt.add_factor("lib:" + notes[k]["lib"], t=qi)
                    flt.add_entry(k, parents=("lib:" + notes[k]["lib"],),
                                  t=qi)
                    registered.add(k)
        envs_cache = {}
        for lib in {notes[k]["lib"] for k in ids}:
            from world import current_env
            en = current_env(probs, lib, q)
            if en and en not in envs_cache:
                envs_cache[en] = GchamEnv(en)
        pre = {k: note_valid(notes[k], envs_cache, probs, q) for k in ids} \
            if cond != "none" else {}

        month = collections.Counter()
        if cond not in ("none", "static"):
            if flt is not None:
                for solved, read in ledger:
                    if read:
                        w = 1.0 / len(read)
                        for nid in read:
                            if nid in notes:
                                flt.update(nid, 1 if solved else 0, t=qi,
                                           msg=None if solved else "env_changed",
                                           weight=w)
            left = float(a.maint_budget)
            for k in sorted(known_broken):
                if left < a.repair_cost:
                    break
                left -= a.repair_cost
                known_broken.discard(k)
                _rep(cond, k, notes, envs_cache, probs, q, flt, qi, month, a)
            if cond == "oracle":
                broken = [x for x in ids if not pre[x]]
                afford = int(left // a.repair_cost)
                todo_rep = broken[:afford]
                left -= a.repair_cost * len(todo_rep)
                # 修复彼此独立, 并行执行 (无限预算的新鲜天花板必须并行)
                with cf.ThreadPoolExecutor(min(a.workers,
                                               max(len(todo_rep), 1))) as rex:
                    list(rex.map(lambda k: _rep(cond, k, notes, envs_cache,
                                                probs, q, flt, qi, month, a),
                                 todo_rep))
            else:
                order = sorted(ids, key=lambda k: -flt.stale_score(k, qi))
                for k in order:
                    if left < 1:
                        break
                    if k in known_broken:
                        continue
                    if flt.stale_score(k, qi) < a.maint_floor:
                        break
                    left -= 1
                    month["probes"] += 1
                    verdict = note_valid(notes[k], envs_cache, probs, q)
                    flt.observe_probe(k, verdict, qi)
                    if verdict and not pre[k]:
                        month["misses"] += 1
                    if not verdict:
                        if pre[k]:
                            month["false_alarms"] += 1
                        if left >= a.repair_cost:
                            left -= a.repair_cost
                            _rep(cond, k, notes, envs_cache, probs, q, flt,
                                 qi, month, a)
                        else:
                            known_broken.add(k)
        ledger = []
        post_valid = sum(note_valid(notes[k], envs_cache, probs, q)
                         for k in ids) if cond != "none" else 0

        todo = [t for t in tasks if t["quarter"] == q]
        if a.n_tasks and stats["n"] + len(todo) > a.n_tasks:
            todo = todo[:max(0, a.n_tasks - stats["n"])]
        recs, sels = [], []
        if todo:
            nl = [notes[k] for k in ids] if cond != "none" else []
            sels = [retrieve(nl, t, a.inject) if nl else [] for t in todo]
            with cf.ThreadPoolExecutor(min(a.workers, max(len(todo), 1))) as ex:
                recs = list(ex.map(lambda tz: exam_one(tz[0], tz[1], a),
                                   zip(todo, sels)))
        n_stale = 0
        for rec, sel in zip(recs, sels):
            rec["condition"] = cond
            rec["stale_injected"] = sum(
                1 for n in sel if not pre.get(n["note_id"], True))
            n_stale += rec["stale_injected"]
            out.write(json.dumps(rec) + "\n")
            stats["n"] += 1
            stats["solved"] += rec["solved"]
            ledger.append((rec["solved"], rec["injected"]))
        out.flush()
        for key in ("probes", "repairs", "repair_ok", "false_alarms",
                    "misses"):
            stats[key] += month[key]
        stats["valid_area"] += post_valid / max(len(ids), 1)
        stats["quarters"] += 1
        for e in envs_cache.values():
            e.cleanup()
        if todo or month["probes"] or month["repairs"]:
            print(f"  [{cond}] {q}: {len(recs)} 题 "
                  f"{sum(r['solved'] for r in recs)} 解出, 笔记本 "
                  f"{post_valid}/{len(ids)} 真, probes {month['probes']} "
                  f"repairs {month['repairs']}(ok {month['repair_ok']}) "
                  f"陈旧注入 {n_stale}, {time.time()-t0:.0f}s", flush=True)
        if a.n_tasks and stats["n"] >= a.n_tasks:
            break
    out.close()
    return stats


def _rep(cond, k, notes, envs_cache, probs, q, flt, qi, month, a):
    from world import current_env
    month["repairs"] += 1
    en = current_env(probs, notes[k]["lib"], q)
    env = GchamEnv(en, call_budget=a.repair_calls)
    try:

        class _A:  # rebirth_note 需要的参数子集
            port, model, repair_calls = a.port, a.model, a.repair_calls
        new, _ = rebirth_note(notes[k], env, _A)
        if new is not None:
            notes[k] = new
        if flt is not None:
            flt.on_repair(k, qi)
        if note_valid(notes[k], envs_cache, probs, q):
            month["repair_ok"] += 1
    finally:
        env.cleanup()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-9B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--tasks", default=os.path.join(
        os.path.dirname(HERE),
        "data/gitchameleon/migrations_tasks_curated.jsonl"))
    ap.add_argument("--conditions", default="none,static,oracle")
    ap.add_argument("--maint-budget", type=float, default=16.0)
    ap.add_argument("--repair-cost", type=float, default=3.0)
    ap.add_argument("--repair-calls", type=int, default=8)
    ap.add_argument("--maint-floor", type=float, default=0.02)
    ap.add_argument("--task-budget", type=int, default=6)
    ap.add_argument("--inject", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--n-tasks", type=int, default=0)
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()

    probs = load_world()
    first_q = {}
    for p in probs:
        k = (p["library"], p["version"])
        if k not in first_q or p["quarter"] < first_q[k]:
            first_q[k] = p["quarter"]
    raw = [json.loads(l) for l in open(a.tasks)]
    tasks = []
    for t in raw:
        c = t["cand"]
        api_tail = c["api"].split(".")[-1]
        alines = assert_lines(t["snippet"])
        if not alines or any(api_tail in l for l in alines):
            continue                        # 断言不可冻结, 后备池
        t["quarter"] = first_q.get((c["lib"], c["new_ver"]))
        t["new_env"] = t.get("new_env")
        if t["quarter"] is None or not t["new_env"]:
            continue
        tasks.append(t)
    exam_qs = sorted({t["quarter"] for t in tasks})
    notes0 = build_notebook(tasks, probs)
    os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)) or ".",
                exist_ok=True)
    conds = [c for c in a.conditions.split(",") if c]
    print(f"T2' 微迁移: {len(tasks)} 道冻结断言题, {len(notes0)} 条种子笔记, "
          f"{len(exam_qs)} 个季度, 条件 {conds}")
    print(f"  -> {a.out_prefix}_<条件>.jsonl", flush=True)

    results = {}
    for cond in conds:
        print(f"== {cond}", flush=True)
        results[cond] = run_condition(cond, a, tasks, notes0, probs, exam_qs)

    nq = max(max(r["quarters"] for r in results.values()), 1)
    print(f"\n{'条件':<15}{'solved':>8}{'笔记本均值':>10}{'probes':>8}"
          f"{'repairs':>9}{'rep_ok':>7}{'FA':>5}{'miss':>6}")
    for cond in conds:
        s = results[cond]
        n = max(s["n"], 1)
        print(f"{cond:<15}{s['solved']/n*100:>7.1f}%"
              f"{s['valid_area']/nq*100:>9.1f}%{s['probes']:>8}"
              f"{s['repairs']:>9}{s['repair_ok']:>7}{s['false_alarms']:>5}"
              f"{s['misses']:>6}")


if __name__ == "__main__":
    main()
