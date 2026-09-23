"""T2 主实验: GitChameleon 季度闭环.

每季度三步, 先结账(上季度错题按 1/n 摊责给注入的笔记), 再维护(预算 B:
探查 1 = 在该库当前版本环境里跑该笔记全部 claim 片段, 确定性算子;
修复 3 = LLM 带 bash 预算进当前环境改写笔记与片段, 可能修不完美),
后考试(每题注入检索 top-4 笔记, agent 在题目钉死版本环境里带预算写代码,
隐藏断言判分)。

条件: none / static / random / ttl / bayesian_agent / ours / oracle。
oracle = 完美探查(直接读机械真值, 不花探查预算) + 与他人相同的 LLM
修复算子, 即"检测完美、修复同水平"的天花板。

指标: 总 pass 率, 对 oracle 的 regret(事后算), 笔记本机械有效率,
探查误报/漏报(确定性算子下来自片段质量), 修复成功率, 预算, 注入陈旧数。
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE)))
from world import load_world, quarters, current_env, GchamEnv     # noqa: E402
from llm import agent_loop                                        # noqa: E402
from bayesian_harness.filters import FilterConfig, build_filter          # noqa: E402

STOP = set("the a an is are to of in on for with and or not if this that it "
           "as at by from will would can could you your we i what which who "
           "when how all any each using use write return given".split())
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def toks(s):
    return {w.lower() for w in IDENT.findall(s or "")
            if w.lower() not in STOP}


def retrieve(notes, prob, k=4):
    """同库优先的关键词检索, 注入 top-k"""
    q = toks(prob["problem"] + " " + prob["starting_code"])
    scored = []
    for n in notes:
        t = toks(" ".join([n["title"], n["text"]] +
                          [c["topic"] for c in n["claims"]]))
        s = len(q & t) / (len(q | t) ** 0.5 or 1)
        same = 2.0 if n["lib"] == prob["library"] else 1.0
        scored.append((s * same, n))
    scored.sort(key=lambda x: -x[0])
    return [n for s, n in scored[:k] if s > 0]


def render_notes(sel):
    if not sel:
        return ""
    parts = []
    for i, n in enumerate(sel, 1):
        cl = "; ".join(f"[{c['topic']}]" for c in n["claims"])
        parts.append(f"### note {i} ({n['kind']}, about {n['lib']})\n"
                     f"{n['title']}\n{n['text']}\nclaims: {cl}")
    return ("\n## Your notes on these libraries\n\n"
            "Written earlier in this project; a library may have moved on "
            "since. Use them as leads, and prefer testing over trusting.\n\n"
            + "\n\n".join(parts) + "\n")


# ------------------------------------------------------------- 机械真值
def note_valid(note, envs_cache, probs, q):
    """一条笔记在季度 q 是否为真 = 全部 claim 片段在其库当前环境退出码 0"""
    env_name = current_env(probs, note["lib"], q)
    if env_name is None:
        return True
    env = envs_cache.get(env_name)
    for c in note["claims"]:
        ok, _ = env.run_py(c["snippet"], timeout=30)
        if not ok:
            return False
    return True


# ------------------------------------------------------------- 修复算子
REPAIR_SYS = """You maintain your own notebook about Python libraries. One of
your notes has gone stale: the library moved to a new version and at least one
of the note's claim snippets now fails. You are inside the CURRENT version's
environment. Investigate with bash (`python -c ...`), one bash code block per
response, each command costs one unit of a small budget.

When done, finish with a single line:

REPAIRED {"text": "<updated 2-4 sentence note text>", "claims": [
  {"lib": "...", "topic": "...", "snippet": "<minimal python that ASSERTS
   the updated behaviour and exits 0 in THIS environment>"}]}

Keep the same number of claims when possible. Hard rules: every snippet
contains at least one `assert` (print proves nothing); normal multi-line
python inside the JSON string (no class/def after semicolons); TEST each
snippet with bash before handing it in."""

REPAIR_USER = """Environment now has {lib}=={ver}.

## The stale note

Title: {title}

{text}

## Its claims (snippet exit codes in the current environment shown)

{claims}

## Budget

{budget} commands.

Update the note so every claim snippet passes here. Finish with REPAIRED."""


REBIRTH_SYS = """You are re-documenting ONE Python library API for your
notebook, inside the CURRENT version's environment. Your old note about it
has gone stale (the library moved). Investigate how it behaves NOW with bash
(`python -c "..."`), one bash code block with one command per response, each
command costs one unit of a small budget.

When done, finish with a single line:

REPAIRED {"text": "<2-4 sentences: how it works in THIS version, or 'removed
in this version, use X instead'>", "claims": [{"topic": "...", "snippet":
"<minimal python that ASSERTS the current behaviour and exits 0 HERE>"}]}

Hard rules: every snippet contains at least one `assert`; normal multi-line
python in the JSON string; TEST each snippet with bash before handing it in;
if the API is gone, the snippet asserts its absence (e.g. hasattr is False
or the import of that name fails)."""

REBIRTH_USER = """Environment now has {lib}=={ver}.
{manual}
## Your stale note (written for an older version)

Title: {title}

{text}

## What its old claims asserted (all failing or suspect now)

{claims}

## Budget

{budget} commands.

Find out the CURRENT behaviour and hand in the REPAIRED line."""


def rebirth_note(note, env, a):
    """重生式修复: 在当前环境重新记录, 产物结构与出生一致"""
    lines = [f"[{c['topic']}]\n{c['snippet'][:300]}" for c in note["claims"]]
    ver = env.name.split("_")[2] if "_" in env.name else "?"
    manual = ""
    if note.get("manual"):
        manual = ("\n## The library's changelog entry (given)\n\n"
                  + note["manual"][:600] + "\n")
    got, _, _ = agent_loop(
        env, a.port, a.model, REBIRTH_SYS,
        REBIRTH_USER.format(lib=note["lib"], ver=ver, title=note["title"],
                            manual=manual,
                            text=note["text"][:600],
                            claims="\n\n".join(lines)[:1800],
                            budget=a.repair_calls),
        "REPAIRED", a.repair_calls + 5, max_tokens=1400)
    calls = env.n_calls
    if not got or not isinstance(got.get("claims"), list):
        return None, calls
    claims = []
    for c in got["claims"][:3]:
        if isinstance(c, dict) and isinstance(c.get("snippet"), str) \
                and "assert" in c["snippet"]:
            claims.append({"lib": note["lib"],
                           "topic": str(c.get("topic") or "")[:120],
                           "snippet": c["snippet"][:1200]})
    if not claims:
        return None, calls
    out = dict(note)
    out["text"] = str(got.get("text") or note["text"])[:900]
    out["claims"] = claims
    return out, calls


def repair_note(note, env, a):
    lines = []
    for c in note["claims"]:
        ok, err = env.run_py(c["snippet"], timeout=30)
        lines.append(f"[{c['topic']}] {'PASS' if ok else 'FAIL ' + err[:100]}\n"
                     f"snippet: {c['snippet'][:400]}")
    ver = env.name.split("_")[2] if "_" in env.name else "?"
    got, _, _ = agent_loop(
        env, a.port, a.model, REPAIR_SYS,
        REPAIR_USER.format(lib=note["lib"], ver=ver, title=note["title"],
                           text=note["text"][:800],
                           claims="\n\n".join(lines)[:2500],
                           budget=a.repair_calls),
        "REPAIRED", a.repair_calls + 5, max_tokens=1400)
    calls = env.n_calls
    if not got or not isinstance(got.get("claims"), list):
        return None, calls
    claims = []
    for c in got["claims"][:4]:
        if isinstance(c, dict) and isinstance(c.get("snippet"), str) \
                and "assert" in c["snippet"]:
            claims.append({"lib": note["lib"],
                           "topic": str(c.get("topic") or "")[:120],
                           "snippet": c["snippet"][:1200]})
    if not claims:
        return None, calls
    out = dict(note)
    out["text"] = str(got.get("text") or note["text"])[:900]
    out["claims"] = claims
    return out, calls


# ------------------------------------------------------------- 考试算子
EXAM_SYS = """You solve a Python coding task inside a pinned virtual
environment. Complete the starting code so the (hidden) tests pass. Test your
approach with bash as you go: `python -c "..."` or write and run a file. Each
command costs one unit of a small budget. Exactly ONE bash code block with ONE
command per response, reasoning before it.

When confident (or out of budget), finish with a single line:

SOLUTION {"code": "<the body completing the starting code, as it should
appear after the given lines>"}"""

EXAM_USER = """Environment: {lib}=={ver} (python {py}){extras}

## Task

{problem}

## Starting code

```python
{starting_code}
```
{notes}
## Budget

{budget} commands.

Complete the code for THIS environment's versions, then hand in SOLUTION."""


def exam_one(prob, sel, a):
    env = GchamEnv(prob["env"], call_budget=a.task_budget)
    try:
        extras = ""
        if prob.get("extra_dependencies"):
            extras = ", extras: " + " ".join(prob["extra_dependencies"])
        got, steps, _ = agent_loop(
            env, a.port, a.model, EXAM_SYS,
            EXAM_USER.format(lib=prob["library"], ver=prob["version"],
                             py=prob["python_version"], extras=extras,
                             problem=prob["problem"][:3000],
                             starting_code=prob["starting_code"][:1500],
                             notes=render_notes(sel),
                             budget=a.task_budget),
            "SOLUTION", a.task_budget + 6, max_tokens=1400)
        code = (got or {}).get("code") or ""
        full = prob["starting_code"] + str(code) + "\n" + prob["test"]
        ok, err = env.run_py(full, timeout=180)
        return {"example_id": prob["example_id"], "lib": prob["library"],
                "quarter": prob["quarter"], "solved": ok,
                "submission": str(code)[:800],
                "tool_calls": env.n_calls, "steps": steps,
                "injected": [n["note_id"] for n in sel],
                "err": "" if ok else err[:150]}
    finally:
        env.cleanup()


# ------------------------------------------------------------- 主循环
def run_condition(cond, a, notes0, probs, exam_qs):
    all_notes = {n["note_id"]: json.loads(json.dumps(n)) for n in notes0}
    flt = None
    if cond not in ("none", "static", "oracle"):
        flt = build_filter(cond, FilterConfig())
    known_broken = set()
    ledger = []
    stats = collections.Counter()
    out = open(f"{a.out_prefix}_{cond}.jsonl", "w")

    registered = set()
    for qi, q in enumerate(exam_qs, start=1):
        t0 = time.time()
        # 滚动出生: 本季度前已出生的笔记才参与 (notes 即主表, 修复直接生效)
        notes = all_notes
        ids = sorted(k for k, v in notes.items()
                     if v.get("born_quarter", "0000Q0") <= q)
        if flt is not None:
            for k in ids:
                if k not in registered:
                    flt.add_factor("lib:" + notes[k]["lib"], t=qi)
                    flt.add_entry(k, parents=("lib:" + notes[k]["lib"],),
                                  t=qi)
                    registered.add(k)
        # 该季度会用到的当前环境, 预热并缓存
        envs_cache = {}
        for lib in {notes[k]["lib"] for k in ids}:
            en = current_env(probs, lib, q)
            if en and en not in envs_cache:
                envs_cache[en] = GchamEnv(en)
        pre = {k: note_valid(notes[k], envs_cache, probs, q) for k in ids} \
            if cond != "none" else {}

        month = collections.Counter()
        if cond not in ("none", "static"):
            # 结账
            if flt is not None:
                for solved, read in ledger:
                    if read:
                        w = 1.0 / len(read)
                        for nid in read:
                            flt.update(nid, 1 if solved else 0, t=qi,
                                       msg=None if solved else "env_changed",
                                       weight=w)
            left = float(a.maint_budget)
            # 上季度确认坏但没钱修的, 先修
            for k in sorted(known_broken):
                if left < a.repair_cost:
                    break
                left -= a.repair_cost
                known_broken.discard(k)
                _do_repair(cond, k, notes, envs_cache, probs, q, flt, qi,
                           month, a)
            if cond == "oracle":
                for k in [x for x in ids if not pre[x]]:
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost
                    _do_repair(cond, k, notes, envs_cache, probs, q, flt,
                               qi, month, a)
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
                    truly = pre[k]
                    if verdict and not truly:
                        month["misses"] += 1     # 片段偶然通过等
                    if not verdict:
                        if truly:
                            month["false_alarms"] += 1
                        if left >= a.repair_cost:
                            left -= a.repair_cost
                            _do_repair(cond, k, notes, envs_cache, probs, q,
                                       flt, qi, month, a)
                        else:
                            known_broken.add(k)
            month["budget_spent"] = a.maint_budget - left
        ledger = []

        # 维护后有效率
        post_valid = sum(note_valid(notes[k], envs_cache, probs, q)
                         for k in ids) if cond != "none" else 0

        # 考试
        todo = [p for p in probs if p["quarter"] == q]
        if a.n_probs and stats["n"] + len(todo) > a.n_probs:
            todo = todo[:max(0, a.n_probs - stats["n"])]
        recs = []
        if todo:
            note_list = [notes[k] for k in ids] if cond != "none" else []
            sels = [retrieve(note_list, p, a.inject) if note_list else []
                    for p in todo]
            with cf.ThreadPoolExecutor(min(a.workers, max(len(todo), 1))) as ex:
                recs = list(ex.map(lambda pz: exam_one(pz[0], pz[1], a),
                                   zip(todo, sels)))
        n_stale_inj = 0
        for rec, sel in zip(recs, sels if todo else []):
            rec["condition"] = cond
            rec["stale_injected"] = sum(1 for n in sel if not pre.get(
                n["note_id"], True))
            n_stale_inj += rec["stale_injected"]
            out.write(json.dumps(rec) + "\n")
            stats["n"] += 1
            stats["solved"] += rec["solved"]
            ledger.append((rec["solved"], rec["injected"]))
        out.flush()
        for key in ("probes", "repairs", "repair_ok", "false_alarms",
                    "misses", "llm_calls"):
            stats[key] += month[key]
        stats["valid_area"] += post_valid / max(len(ids), 1)
        stats["quarters"] += 1
        for e in envs_cache.values():
            e.cleanup()
        print(f"  [{cond}] {q}: {len(recs)} 题 "
              f"{sum(r['solved'] for r in recs)} 解出, 笔记本 "
              f"{post_valid}/{len(ids)} 真, probes {month['probes']} "
              f"repairs {month['repairs']}(ok {month['repair_ok']}) "
              f"fa {month['false_alarms']} miss {month['misses']} "
              f"陈旧注入 {n_stale_inj}, {time.time()-t0:.0f}s", flush=True)
        if a.n_probs and stats["n"] >= a.n_probs:
            break
    out.close()
    return stats


def _do_repair(cond, k, notes, envs_cache, probs, q, flt, qi, month, a):
    month["repairs"] += 1
    en = current_env(probs, notes[k]["lib"], q)
    env = GchamEnv(en, call_budget=a.repair_calls)
    try:
        op = rebirth_note if a.repair_op == "rebirth" else repair_note
        new, calls = op(notes[k], env, a)
        month["llm_calls"] += calls
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
    ap.add_argument("--notebook", default=os.path.join(HERE,
                                                       "notebook_birth.jsonl"))
    ap.add_argument("--conditions",
                    default="none,static,random,ttl,bayesian_agent,ours,oracle")
    ap.add_argument("--maint-budget", type=float, default=8.0)
    ap.add_argument("--repair-cost", type=float, default=3.0)
    ap.add_argument("--repair-calls", type=int, default=8)
    ap.add_argument("--repair-op", default="rebirth",
                    choices=["rebirth", "edit"],
                    help="rebirth=当前环境重新记录(默认); edit=交互式改写(消融)")
    ap.add_argument("--maint-floor", type=float, default=0.02)
    ap.add_argument("--task-budget", type=int, default=8)
    ap.add_argument("--inject", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--n-probs", type=int, default=0, help="0 = 全部")
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()

    probs = load_world()
    exam_qs = quarters(probs)
    notes0 = [json.loads(l) for l in open(a.notebook)]
    os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)) or ".",
                exist_ok=True)
    conds = [c for c in a.conditions.split(",") if c]
    n_exam = sum(1 for p in probs if p["quarter"] >= exam_qs[0])
    print(f"T2 闭环: {len(notes0)} 条笔记, {n_exam} 道考题 x {len(conds)} 条件, "
          f"维护 {a.maint_budget:g}/季 任务 {a.task_budget}/题 注入 {a.inject}")
    print(f"  -> {a.out_prefix}_<条件>.jsonl", flush=True)

    results = {}
    for cond in conds:
        print(f"== {cond}", flush=True)
        results[cond] = run_condition(cond, a, notes0, probs, exam_qs)

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
