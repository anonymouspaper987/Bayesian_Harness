"""出题 + 断裂矩阵 + 流量日程 + 笔记种子.

tasks.jsonl   每个 M01 存活 API 三道变体 (旧版通过), 附 36 个月的通过/失败矩阵
exams.jsonl   按月 Zipf 抽样的考试日程, 只排"此刻已坏"的遭遇
notes_M01.jsonl  内部笔记种子: 每 API 一张, 内容为 M01 验证过的用法片段
"""
import argparse
import collections
import concurrent.futures as cf
import io
import json
import os
import random
import sys
import types
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kernels import call_api, public_kwargs, make_module, dump_table  # noqa: E402
from spec import sample_inputs, SUBSYSTEMS                            # noqa: E402
from sworld import SynthEnv, seed_dir                                   # noqa: E402


def render_call(spec, kw, variant):
    """三种调用风格: 0 全位置, 1 全关键字(省略有默认值的), 2 首位置其余关键字."""
    core = [p for p in spec["params"] if not p.get("extra")]
    extras = [p for p in spec["params"] if p.get("extra")]
    if variant == 0:
        parts = [repr(kw[p["n"]]) for p in core]
        parts += [f"{p['n']}={kw[p['n']]!r}" for p in extras]
    elif variant == 1:
        parts = [f"{p['n']}={kw[p['n']]!r}" for p in spec["params"] if "d" not in p]
    else:
        parts = [repr(kw[core[0]["n"]])] if core else []
        parts += [f"{p['n']}={kw[p['n']]!r}" for p in core[1:]]
        parts += [f"{p['n']}={kw[p['n']]!r}" for p in extras]
    return f"{spec['name']}({', '.join(parts)})"


def make_snippet(spec, kw, variant, expected):
    call = render_call(spec, kw, variant)
    return (f"from acmesdk.{spec['sub']} import {spec['name']}\n"
            f"result = {call}\n"
            f"assert result == {expected!r}\n"
            f"print('Success')\n")


def eval_kwargs(spec, kw, variant):
    """变体 1 省略默认参数, 期望值要按默认算."""
    if variant == 1:
        return {k: v for k, v in kw.items()
                if not any(p["n"] == k and "d" in p for p in spec["params"])}
    return kw


# ---------------- 进程内模拟: 用月规格搭假包, exec 片段 ----------------
def _fake_pkg(state):
    pkg = types.ModuleType("acmesdk")
    pkg.__path__ = []
    mods = {"acmesdk": pkg}
    for sub in SUBSYSTEMS:
        table = {a["name"]: {"kernel": a["kernel"], "knobs": a["knobs"], "shape": a["shape"],
                             "params": a["params"], "perm": a.get("perm")}
                 for a in state.values() if a["sub"] == sub and a["alive"]}
        proxy = make_module(f"acmesdk.{sub}", dump_table(table))
        mods[f"acmesdk.{sub}"] = proxy
        setattr(pkg, sub, proxy)
    return mods


def sim_run(snippet, mods):
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        with redirect_stdout(io.StringIO()):
            exec(compile(snippet, "<task>", "exec"), {"__name__": "__task__"})
        return True
    except BaseException:
        return False
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--per-month", type=int, default=10)
    ap.add_argument("--zipf", type=float, default=1.0)
    ap.add_argument("--spot-check", type=int, default=40, help="抽多少道题用真实子进程复核")
    a = ap.parse_args()
    sd = seed_dir(a.seed)
    rng = random.Random(a.seed * 31 + 5)
    snaps = {m: json.load(open(f"{sd}/truth/spec_M{m:02d}.json")) for m in range(1, a.months + 1)}
    state1 = snaps[1]

    # 1) 出题 (M01 规格, 进程内算期望值)
    tasks = []
    for aid, spec in sorted(state1.items()):
        for v in range(3):
            ins = sample_inputs(spec["kernel"], rng)
            kw = public_kwargs(spec, ins, token=rng.choice(["tok-1", "sess-9", "key-42"]))
            try:
                expected = call_api(spec["name"], spec, (), eval_kwargs(spec, kw, v))
            except Exception:
                continue
            tasks.append({"task_id": f"{aid}#v{v}", "api_id": aid, "sub": spec["sub"],
                          "api": spec["name"], "variant": v,
                          "snippet": make_snippet(spec, kw, v, expected),
                          "expected": repr(expected)})
    print(f"出题 {len(tasks)} 道 (API {len(state1)} 个)", flush=True)

    # 2) 断裂矩阵 (进程内)
    fakes = {m: _fake_pkg(snaps[m]) for m in range(1, a.months + 1)}
    for t in tasks:
        t["pass_by_month"] = [sim_run(t["snippet"], fakes[m]) for m in range(1, a.months + 1)]
    bad_birth = [t for t in tasks if not t["pass_by_month"][0]]
    tasks = [t for t in tasks if t["pass_by_month"][0]]
    ever_break = sum(1 for t in tasks if not all(t["pass_by_month"]))
    print(f"M01 不通过剔除 {len(bad_birth)} 道; 剩 {len(tasks)} 道, 其中 36 个月内会坏 {ever_break} 道", flush=True)
    for t in tasks:
        fb = next((i + 1 for i, ok in enumerate(t["pass_by_month"]) if not ok), None)
        t["first_break"] = fb

    # 3) 真实子进程抽检 (进程内模拟 == 字节码版本)
    sample = rng.sample(tasks, min(a.spot_check, len(tasks)))
    mism = 0

    def check(t):
        bad = 0
        for m in (1, t["first_break"] or a.months, a.months):
            env = SynthEnv(a.seed, m)
            try:
                ok, _ = env.run_py(t["snippet"])
            finally:
                env.cleanup()
            if ok != t["pass_by_month"][m - 1]:
                bad += 1
        return bad
    with cf.ThreadPoolExecutor(6) as ex:
        mism = sum(ex.map(check, sample))
    print(f"抽检 {len(sample)} 道 x 3 个月份, 进程内与真实环境不一致 {mism} 处", flush=True)

    # 4) 流量日程: Zipf 热度, 只排此刻已坏的遭遇
    api_ids = sorted({t["api_id"] for t in tasks})
    rng.shuffle(api_ids)
    pop = {aid: 1.0 / (r + 1) ** a.zipf for r, aid in enumerate(api_ids)}
    by_api = collections.defaultdict(list)
    for t in tasks:
        by_api[t["api_id"]].append(t)
    exams, enc = [], collections.Counter()
    for m in range(2, a.months + 1):
        cands = [aid for aid in api_ids if any(not t["pass_by_month"][m - 1] for t in by_api[aid])]
        if not cands:
            continue
        chosen, pool = [], list(cands)
        while pool and len(chosen) < a.per_month:
            w = [pop[x] for x in pool]
            pick = rng.choices(pool, weights=w)[0]
            pool.remove(pick)
            chosen.append(pick)
        for aid in chosen:
            broken = [t for t in by_api[aid] if not t["pass_by_month"][m - 1]]
            t = broken[enc[aid] % len(broken)]
            exams.append({"m": m, "task_id": t["task_id"], "api_id": aid, "sub": t["sub"],
                          "encounter": enc[aid]})
            enc[aid] += 1
    rep = sum(1 for e in exams if e["encounter"] > 0)
    print(f"考试 {len(exams)} 次, 其中重复遭遇 {rep} 次, 涉及 API {len(enc)} 个, "
          f"最热 API 被考 {max(enc.values())} 次", flush=True)

    # 5) 笔记种子 (变体 0 的片段)
    notes = []
    for aid, spec in sorted(state1.items()):
        t0 = next((t for t in tasks if t["api_id"] == aid and t["variant"] == 0), None)
        if not t0:
            continue
        notes.append({"note_id": aid, "sub": spec["sub"], "api": spec["name"],
                      "title": f"acmesdk.{spec['sub']}.{spec['name']} usage (1.1.0)",
                      "text": f"Validated usage of acmesdk.{spec['sub']}.{spec['name']} "
                              f"as of acmesdk 1.1.0:\n{t0['snippet']}",
                      "snippet": t0["snippet"], "born": 1})

    with open(f"{sd}/tasks.jsonl", "w") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    with open(f"{sd}/exams.jsonl", "w") as f:
        for e in exams:
            f.write(json.dumps(e) + "\n")
    with open(f"{sd}/notes_M01.jsonl", "w") as f:
        for n in notes:
            f.write(json.dumps(n) + "\n")
    print(f"写出 tasks/exams/notes_M01 到 {sd}")


if __name__ == "__main__":
    main()
