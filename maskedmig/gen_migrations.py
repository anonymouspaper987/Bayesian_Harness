"""微迁移工序 2+3: 旧环境写用法片段 + 双环境机器验证.

每个候选断裂点: LLM 在旧版环境里写一段最小可运行代码, 真实调用该 API 并
断言其行为, 当场测试通过才交卷 (工序2)。然后同一段代码拿到新版环境运行,
必须失败才成题 (工序3, 装饰性变化在此自然出局)。
产出 migrations_tasks.jsonl, 字段含题面素材、片段、两端验证证据。
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
from world import load_world, GchamEnv                            # noqa: E402
from llm import agent_loop                                        # noqa: E402

SYS = """You are pinning down the behaviour of ONE Python API inside a pinned
virtual environment, producing a small runnable proof.

Write a minimal self-contained snippet that ACTUALLY CALLS the API below on a
tiny example and ASSERTS something about its result or effect. Test it with
bash (`python -c "..."` or write and run a file) before handing it in. Each
command costs one unit of a small budget. Exactly ONE bash code block with ONE
command per response.

Hard rules for the snippet:
- it must import what it needs and call the API (hasattr alone proves nothing);
- at least one `assert` on the result/behaviour;
- normal multi-line python (use \\n in the JSON string);
- cheap: subsecond, no network, no large files;
- it must exit 0 in THIS environment.

When it passes here, finish with a single line:

SNIPPET {"code": "<the snippet>"}"""

USER = """Environment: {lib}=={ver} (python here has it installed).

## The API

{api}
signature: {sig}

## Budget

{budget} commands.

Write and test the snippet, then hand in the SNIPPET line."""


def env_map():
    probs = load_world()
    m = {}
    for p in probs:
        m.setdefault((p["library"], p["version"]), []).append(p["env"])
    return {k: sorted(v, key=len)[0] for k, v in m.items()}


def one(cand, a, emap):
    old_env_name = emap.get((cand["lib"], cand["old_ver"]))
    new_env_name = emap.get((cand["lib"], cand["new_ver"]))
    if not old_env_name or not new_env_name:
        return None
    env = GchamEnv(old_env_name, call_budget=a.budget)
    try:
        got, steps, _ = agent_loop(
            env, a.port, a.model, SYS,
            USER.format(lib=cand["lib"], ver=cand["old_ver"],
                        api=cand["api"], sig=cand.get("old_sig") or "?",
                        budget=a.budget),
            "SNIPPET", a.budget + 5, max_tokens=1200)
        code = (got or {}).get("code")
        if not isinstance(code, str) or "assert" not in code:
            return {"cand": cand, "status": "no_snippet"}
        old_ok, old_err = env.run_py(code, timeout=60)
        if not old_ok:
            return {"cand": cand, "status": "old_fail", "err": old_err[:150]}
    finally:
        env.cleanup()
    env2 = GchamEnv(new_env_name)
    try:
        new_ok, new_err = env2.run_py(code, timeout=60)
    finally:
        env2.cleanup()
    if new_ok:
        return {"cand": cand, "status": "still_passes"}   # 装饰性变化, 出局
    return {"cand": cand, "status": "task",
            "snippet": code, "new_err": new_err[:300],
            "old_env": old_env_name, "new_env": new_env_name}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-9B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--budget", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cands", default=os.path.join(
        os.path.dirname(HERE),
        "data/gitchameleon/migrations_candidates.jsonl"))
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(HERE),
        "data/gitchameleon/migrations_tasks.jsonl"))
    a = ap.parse_args()

    cands = [json.loads(l) for l in open(a.cands)]
    done_keys = set()
    if os.path.exists(a.out):
        for l in open(a.out):
            r = json.loads(l)
            c = r["cand"]
            done_keys.add((c["lib"], c["old_ver"], c["new_ver"], c["api"]))
    todo = [c for c in cands
            if (c["lib"], c["old_ver"], c["new_ver"], c["api"]) not in done_keys]
    if a.limit:
        todo = todo[:a.limit]
    emap = env_map()
    print(f"候选 {len(cands)}, 已处理 {len(done_keys)}, 本轮 {len(todo)}",
          flush=True)

    t0 = time.time()
    stat = collections.Counter()
    with open(a.out, "a") as fh, cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(lambda c: one(c, a, emap), todo):
            if res is None:
                stat["no_env"] += 1
                continue
            stat[res["status"]] += 1
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            n = sum(stat.values())
            if n % 50 == 0:
                print(f"  [{n}/{len(todo)}] {dict(stat)} "
                      f"{(time.time()-t0)/60:.0f}min", flush=True)
    print(f"\n完成: {dict(stat)}, 成题 {stat['task']} 道, "
          f"{(time.time()-t0)/60:.0f} 分钟")


if __name__ == "__main__":
    main()
