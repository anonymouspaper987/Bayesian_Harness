"""行为验证接替者 + 家族去重, 供掩码对照使用 (独立脚本, 夜班链可调)."""
import json, re, os, sys, difflib
import concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world import GchamEnv
from fresh_pipeline import FTARS

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRESH = os.environ.get("MASKEDMIG_DATA") or os.path.join(_REPO, "data", "maskedmig")
_ENVS = os.environ.get("MIG_ENVS") or os.path.join(_REPO, "data", "mig_envs")
DUMPS = os.path.join(_ENVS, "api_dumps_v2")
tasks = [json.loads(l) for l in open(f"{FRESH}/fresh_tasks.jsonl") if '"task"' in l]
tasks = [t for t in tasks if t["status"] == "task" and t["cand"]["kind"] == "removed"]
notes = {}
for fn in ("band_notes.jsonl", "band_notes_v3.jsonl"):
    try:
        for l in open(f"{FRESH}/{fn}"):
            r = json.loads(l)
            notes.setdefault((r["lib"], r["api"]), r)
    except FileNotFoundError:
        pass

def api_dump(lib, ver):
    f = os.path.join(DUMPS, f"fresh_{lib}_{ver}_py3.12.json")
    return json.load(open(f)) if os.path.exists(f) else {}

def candidates(t):
    c = t["cand"]
    old_tail = c["api"].split(".")[-1]
    tails = {a.split(".")[-1] for a in api_dump(c["lib"], c["new_ver"])}
    cands = set()
    fr = notes.get((c["lib"], c["api"]), {}).get("fresh_note", {})
    text = (fr.get("text") or "") + "\n".join(cl.get("snippet") or "" for cl in (fr.get("claims") or []))
    for m in re.finditer(r"\b([A-Za-z_]\w{2,})\s*\(", text):
        if m.group(1) in tails and m.group(1) != old_tail:
            cands.add(m.group(1))
    cands |= set(difflib.get_close_matches(old_tail, tails, n=10, cutoff=0.5))
    parent = ".".join(c["api"].split(".")[:-1])
    sibs = [a.split(".")[-1] for a in api_dump(c["lib"], c["new_ver"])
            if a.startswith(parent + ".")]
    cands |= set(difflib.get_close_matches(old_tail, sibs, n=6, cutoff=0.4))
    cands.discard(old_tail)
    return sorted(cands)[:15]

def verify(t):
    c = t["cand"]
    old_tail = c["api"].split(".")[-1]
    cds = candidates(t)
    if not cds:
        return None
    env = GchamEnv(t["new_env"], tars_dir=FTARS)
    try:
        for cd in cds:
            swapped = re.sub(r"\b" + re.escape(old_tail) + r"\b", cd, t["snippet"])
            ok, _ = env.run_py(swapped, timeout=45)
            if ok:
                return (c["lib"], c["api"], cd)
        return None
    finally:
        env.cleanup()

if __name__ == "__main__":   # 只在直接运行时写文件, 避免被 import 时就动手
    verified = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(verify, tasks):
            if res:
                lib, api, cd = res
                verified[f"{lib}|{api}"] = [cd]
    json.dump(verified, open(f"{FRESH}/successors_verified.json", "w"), indent=1)
    seen, dedup = set(), {}
    for k, succ in verified.items():
        lib, api = k.split("|", 1)
        key = (lib, api.split(".")[-1], succ[0])
        if key not in seen:
            seen.add(key)
            dedup[k] = succ
    json.dump(dedup, open(f"{FRESH}/successors_verified_dedup.json", "w"), indent=1)
    print(f"行为验证 {len(verified)}/{len(tasks)}, 去重家族 {len(dedup)}")
