"""命令行世界 (C1) 出题: 与 make_tasks.py 完全相同的随机序列 -> 同一批 (API, 输入, 变体), 只把片段渲染成 shell + acmectl.
断裂矩阵用进程内模拟 (cli_surface.sim_run_sh), 真实 bash 抽检, 考试日程用同一套 Zipf 抽样 (随机数消耗一致, 所以与 Python 世界考同一批题).
写出 tasks_cli.jsonl / exams_cli.jsonl / notes_cli_M01.jsonl 到同一个 seed 目录.
    python3 make_tasks_cli.py --seed 1
"""
import argparse, collections, concurrent.futures as cf, json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from kernels import call_api, public_kwargs                          # noqa: E402
from spec import sample_inputs                                       # noqa: E402
from sworld import seed_dir                                          # noqa: E402
from make_tasks import _fake_pkg, eval_kwargs                        # noqa: E402
import cli_surface as C                                              # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--per-month", type=int, default=10)
    ap.add_argument("--zipf", type=float, default=1.0)
    ap.add_argument("--spot-check", type=int, default=40)
    a = ap.parse_args()
    sd = seed_dir(a.seed)
    rng = random.Random(a.seed * 31 + 5)                             # 与 make_tasks 同一个种子, 同一消耗顺序
    snaps = {m: json.load(open(f"{sd}/truth/spec_M{m:02d}.json")) for m in range(1, a.months + 1)}
    state1 = snaps[1]

    tasks = []
    for aid, spec in sorted(state1.items()):
        for v in range(3):
            ins = sample_inputs(spec["kernel"], rng)
            kw = public_kwargs(spec, ins, token=rng.choice(["tok-1", "sess-9", "key-42"]))
            try:
                expected = call_api(spec["name"], spec, (), eval_kwargs(spec, kw, v))
            except Exception:
                continue
            tasks.append({"task_id": f"{aid}#v{v}", "api_id": aid, "sub": spec["sub"], "api": spec["name"],
                          "variant": v, "snippet": C.make_snippet_sh(spec, kw, v, expected),
                          "expected": C.render_out(expected)})
    print(f"出题 {len(tasks)} 道 (API {len(state1)} 个)", flush=True)

    fakes = {m: _fake_pkg(snaps[m]) for m in range(1, a.months + 1)}
    unparsed = 0
    for t in tasks:
        row = []
        for m in range(1, a.months + 1):
            r = C.sim_run_sh(t["snippet"], fakes[m])
            if r is None:
                unparsed += 1; r = False
            row.append(r)
        t["pass_by_month"] = row
    bad_birth = [t for t in tasks if not t["pass_by_month"][0]]
    tasks = [t for t in tasks if t["pass_by_month"][0]]
    ever_break = sum(1 for t in tasks if not all(t["pass_by_month"]))
    print(f"M01 不通过剔除 {len(bad_birth)} 道; 剩 {len(tasks)} 道, 其中 36 个月内会坏 {ever_break} 道; 模拟器解析不了 {unparsed} 处", flush=True)
    for t in tasks:
        t["first_break"] = next((i + 1 for i, ok in enumerate(t["pass_by_month"]) if not ok), None)

    sample = rng.sample(tasks, min(a.spot_check, len(tasks)))

    def check(t):
        bad = 0
        for m in (1, t["first_break"] or a.months, a.months):
            env = C.CliEnv(a.seed, m)
            try:
                ok, _ = env.run_sh(t["snippet"])
            finally:
                env.cleanup()
            if ok != t["pass_by_month"][m - 1]:
                bad += 1
        return bad
    with cf.ThreadPoolExecutor(6) as ex:
        mism = sum(ex.map(check, sample))
    print(f"抽检 {len(sample)} 道 x 3 个月份, 进程内模拟与真实 bash 不一致 {mism} 处", flush=True)

    # 断裂矩阵若与 Python 世界逐题相同, 直接复用它的考试日程: 两种表面考同一批 (题, 月), 可按题配对
    py_tasks = {}
    if os.path.exists(f"{sd}/tasks.jsonl") and os.path.exists(f"{sd}/exams.jsonl"):
        py_tasks = {t["task_id"]: t for t in map(json.loads, open(f"{sd}/tasks.jsonl"))}
    aligned = bool(py_tasks) and set(py_tasks) == {t["task_id"] for t in tasks} and \
        all(py_tasks[t["task_id"]]["pass_by_month"] == t["pass_by_month"] for t in tasks)
    if aligned:
        exams = [json.loads(l) for l in open(f"{sd}/exams.jsonl")]
        enc = collections.Counter(e["api_id"] for e in exams)
        print(f"断裂矩阵与 Python 世界逐题相同, 复用其考试日程 ({len(exams)} 次)", flush=True)
    api_ids = sorted({t["api_id"] for t in tasks})
    rng.shuffle(api_ids)
    pop = {aid: 1.0 / (r + 1) ** a.zipf for r, aid in enumerate(api_ids)}
    by_api = collections.defaultdict(list)
    for t in tasks:
        by_api[t["api_id"]].append(t)
    if not aligned:
        exams, enc = [], collections.Counter()
    for m in (range(2, a.months + 1) if not aligned else []):
        cands = [aid for aid in api_ids if any(not t["pass_by_month"][m - 1] for t in by_api[aid])]
        if not cands:
            continue
        chosen, pool = [], list(cands)
        while pool and len(chosen) < a.per_month:
            w = [pop[x] for x in pool]
            pick = rng.choices(pool, weights=w)[0]
            pool.remove(pick); chosen.append(pick)
        for aid in chosen:
            broken = [t for t in by_api[aid] if not t["pass_by_month"][m - 1]]
            t = broken[enc[aid] % len(broken)]
            exams.append({"m": m, "task_id": t["task_id"], "api_id": aid, "sub": t["sub"], "encounter": enc[aid]})
            enc[aid] += 1
    rep = sum(1 for e in exams if e["encounter"] > 0)
    print(f"考试 {len(exams)} 次, 其中重复遭遇 {rep} 次, 涉及 API {len(enc)} 个, 最热 API 被考 {max(enc.values())} 次" + (" (复用 Python 日程)" if aligned else ""), flush=True)

    notes = []
    for aid, spec in sorted(state1.items()):
        t0 = next((t for t in tasks if t["api_id"] == aid and t["variant"] == 0), None)
        if not t0:
            continue
        notes.append({"note_id": aid, "sub": spec["sub"], "api": spec["name"],
                      "title": f"acmectl {spec['sub']} {spec['name']} usage (1.1.0)",
                      "text": f"Validated usage of acmectl {spec['sub']} {spec['name']} as of acmectl 1.1.0:\n{t0['snippet']}",
                      "snippet": t0["snippet"], "born": 1})

    for name, rows in (("tasks", tasks), ("exams", exams), ("notes", notes)):
        with open(f"{sd}/{C.FILES[name]}", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    # 与 Python 世界的考试日程对齐度
    py = [json.loads(l) for l in open(f"{sd}/exams.jsonl")] if os.path.exists(f"{sd}/exams.jsonl") else []
    same = sum(1 for e1, e2 in zip(exams, py) if (e1["m"], e1["api_id"]) == (e2["m"], e2["api_id"]))
    print(f"写出 {C.FILES['tasks']} / {C.FILES['exams']} / {C.FILES['notes']} 到 {sd}; 与 Python 世界考试日程逐条相同 {same}/{max(len(py),1)}")


if __name__ == "__main__":
    main()
