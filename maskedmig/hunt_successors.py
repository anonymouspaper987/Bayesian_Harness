"""LLM 提名接班函数 + 机器裁决 (换名试跑通过才收).

对行为验证没认出接班的题: agent 进新环境勘察 "X 被删了, 最接近的替代
是谁", 提名后用同一套换名试跑机械验证, 通过才写入 verified 名单。
LLM 只有提名权, 没有裁决权, 零污染。
"""
import json, re, os, sys
import concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world import GchamEnv
from fresh_pipeline import FTARS
from llm import agent_loop

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRESH = os.environ.get("MASKEDMIG_DATA") or os.path.join(_REPO, "data", "maskedmig")

SYS = """A Python API was removed from a library. You are inside the NEW
version's environment. Find the closest current replacement: the function or
method a maintainer would migrate the old call to. Investigate with bash
(`python -c "..."`: try dir() on the module/class, try candidate calls), one
bash code block per response, each command costs one unit of a small budget.

When confident, finish with a single line:

SUCCESSOR {"name": "<bare name of the replacement, e.g. concat>",
           "how": "<one line: how the call changes, if at all>"}"""

USER = """Library {lib}, now at version {new_ver}.

Removed API: {api}
Its old usage (worked on {old_ver}, fails now):

```python
{snippet}
```

## Budget

{budget} commands.

Find the current replacement and hand in the SUCCESSOR line."""


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-9B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    verified = json.load(open(f"{FRESH}/successors_verified.json"))
    tasks = [json.loads(l) for l in open(f"{FRESH}/fresh_tasks.jsonl")
             if '"task"' in l]
    tasks = [t for t in tasks if t["status"] == "task"
             and t["cand"]["kind"] == "removed"
             and f"{t['cand']['lib']}|{t['cand']['api']}" not in verified]
    print(f"待猎接班: {len(tasks)} 题", flush=True)

    def hunt(t):
        c = t["cand"]
        old_tail = c["api"].split(".")[-1]
        env = GchamEnv(t["new_env"], call_budget=a.budget, tars_dir=FTARS)
        try:
            got, _, _ = agent_loop(
                env, a.port, a.model, SYS,
                USER.format(lib=c["lib"], new_ver=c["new_ver"],
                            old_ver=c["old_ver"], api=c["api"],
                            snippet=t["snippet"][:1200], budget=a.budget),
                "SUCCESSOR", a.budget + 4, max_tokens=900)
            name = str((got or {}).get("name") or "").strip().split(".")[-1]
            if not name or name == old_tail or not re.match(r"^\w+$", name):
                return None
            swapped = re.sub(r"\b" + re.escape(old_tail) + r"\b", name,
                             t["snippet"])
            ok, _ = env.run_py(swapped, timeout=45)
            return (c["lib"], c["api"], name) if ok else None
        finally:
            env.cleanup()

    won = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(hunt, tasks):
            if res:
                lib, api, name = res
                verified[f"{lib}|{api}"] = [name]
                won += 1
    json.dump(verified, open(f"{FRESH}/successors_verified.json", "w"),
              indent=1)
    seen, dedup = set(), {}
    for k, succ in verified.items():
        lib, api = k.split("|", 1)
        key = (lib, api.split(".")[-1], succ[0])
        if key not in seen:
            seen.add(key)
            dedup[k] = succ
    json.dump(dedup, open(f"{FRESH}/successors_verified_dedup.json", "w"),
              indent=1)
    print(f"猎获并通过机器裁决 {won} 题; 名单总量 {len(verified)}, "
          f"去重家族 {len(dedup)}")


if __name__ == "__main__":
    main()
