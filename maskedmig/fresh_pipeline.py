"""后截止期题池流水线: 差分 -> 过滤 -> (GPU)片段生成 -> 双验证 -> 带宽测试.

子命令:
  mine   在 22 对新旧环境里枚举 API (含 __module__) 并差分, 产出
         fresh_inventory.jsonl; 过滤规则同 v2 (泄漏/类派生/基础设施/内部).
  gen    LLM 在旧环境写真实调用片段 (含 assert, 当场测), 新环境必挂才成题,
         产出 fresh_tasks.jsonl。要求 vLLM 在本机 8000。
  band   三条件单发对照: none / static(注入旧笔记) / fresh(注入新环境重生
         的笔记), 每题一集, 冻结断言判分, 直接回答 none-oracle 带宽多大。
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from world import GchamEnv                                        # noqa: E402
from llm import agent_loop                                        # noqa: E402
from filter_migrations_v2 import classify, split_sig              # noqa: E402
from gen_migrations import SYS as GEN_SYS, USER as GEN_USER       # noqa: E402
from migration_loop import (EXAM_SYS, EXAM_USER, assert_lines,    # noqa: E402
                            grade)
from quarter_loop import rebirth_note                             # noqa: E402

REPO = os.path.dirname(HERE)
FRESH = os.environ.get("MASKEDMIG_DATA") or os.path.join(REPO, "data", "maskedmig")
ENVS = os.environ.get("MIG_ENVS") or os.path.join(REPO, "data", "mig_envs")
FTARS = os.path.join(ENVS, "fresh_tars")
DUMPS = os.path.join(ENVS, "api_dumps_v2")
IMPORT_NAME = {"scikit-learn": "sklearn", "pillow": "PIL",
               "langchain-core": "langchain_core",
               "huggingface-hub": "huggingface_hub"}

DUMP = open(os.path.join(HERE, "mine_migrations.py")).read()
DUMP = DUMP.split("DUMP = r'''")[1].split("'''")[0]


def need(path):
    """语料/环境不随仓库发布, 缺了就给一句人能看懂的话, 不要甩 traceback."""
    if not os.path.exists(path):
        raise SystemExit(
            f"missing: {path}\n"
            "The maskedmig corpus is not shipped with this repo. Build it with the\n"
            "mining pipeline (README, 'Building the maskedmig corpus'), or point\n"
            "MASKEDMIG_DATA at a copy you already have.")
    return path


_PAIRS = None


def pairs():
    """18 个库的 (旧版, 新版) 版本对. 第一次用到时才读, 读完缓存."""
    global _PAIRS
    if _PAIRS is None:
        _PAIRS = json.load(open(need(os.path.join(FRESH, "pairs.json"))))
    return _PAIRS


def env_name(lib, ver):
    return f"fresh_{lib}_{ver}_py3.12".replace("/", "_")


def dump_api(lib, ver):
    cpath = os.path.join(DUMPS, f"{env_name(lib, ver)}.json")
    if os.path.exists(cpath):
        return json.load(open(cpath))
    try:
        env = GchamEnv(env_name(lib, ver), tars_dir=FTARS)
    except FileNotFoundError:
        print(f"  环境缺失 {lib}=={ver}, 跳过")
        return None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=env.workdir,
                                         delete=False) as f:
            f.write(DUMP)
            tmp = f.name
        outp = os.path.join(env.workdir, "api.json")
        cp = subprocess.run([env.python, tmp, IMPORT_NAME.get(lib, lib), outp],
                            capture_output=True, text=True, timeout=600,
                            cwd=env.workdir)
        try:
            api = json.load(open(outp))
        except Exception:                                        # noqa: BLE001
            print(f"  dump 失败 {lib}=={ver}: {(cp.stderr or '')[-150:]}")
            return None
        json.dump(api, open(cpath, "w"))
        return api
    finally:
        env.cleanup()


def cmd_mine(a):
    out = open(f"{FRESH}/fresh_inventory.jsonl", "w")
    n = 0
    for p in pairs():
        a0 = dump_api(p["lib"], p["old_ver"])
        a1 = dump_api(p["lib"], p["new_ver"])
        if not a0 or not a1:
            continue
        removed = sorted(set(a0) - set(a1))
        sig_diff = sorted(q for q in set(a0) & set(a1)
                          if a0[q] != a1[q])
        print(f"{p['lib']:14s} {p['old_ver']} -> {p['new_ver']}: "
              f"API {len(a0)} -> {len(a1)}, 消失 {len(removed)}, "
              f"变化 {len(sig_diff)}", flush=True)
        for q in removed:
            out.write(json.dumps({"lib": p["lib"], "old_ver": p["old_ver"],
                                  "new_ver": p["new_ver"],
                                  "date": p["new_date"], "kind": "removed",
                                  "api": q, "old_sig": a0[q]}) + "\n")
            n += 1
        for q in sig_diff:
            out.write(json.dumps({"lib": p["lib"], "old_ver": p["old_ver"],
                                  "new_ver": p["new_ver"],
                                  "date": p["new_date"], "kind": "sig_diff",
                                  "api": q, "old_sig": a0[q],
                                  "new_sig": a1[q]}) + "\n")
            n += 1
    out.close()
    # 过滤 (v2 规则) + 限额
    rows = [json.loads(l) for l in open(f"{FRESH}/fresh_inventory.jsonl")]
    removed_set = {(r["lib"], r["old_ver"], r["new_ver"], r["api"])
                   for r in rows if r["kind"] == "removed"}
    kept, drops = [], collections.Counter()
    for r in rows:
        why = classify(r, removed_set)
        (drops.update([why]) if why else kept.append(r))
    byg = collections.defaultdict(list)
    for r in kept:
        byg[(r["lib"], r["kind"])].append(r)
    final = []
    for key, rs in byg.items():
        cap = 9999 if key[1] == "removed" else 10
        rs.sort(key=lambda x: (len(x["api"].split(".")), len(x["api"])))
        final.extend(rs[:cap])
    with open(f"{FRESH}/fresh_candidates.jsonl", "w") as f:
        for r in final:
            f.write(json.dumps(r) + "\n")
    print(f"断裂点 {n} -> 规则后 {len(kept)} -> 限额后 {len(final)}")
    print("剔除:", dict(drops.most_common(6)))


def cmd_gen(a):
    cands = [json.loads(l) for l in open(f"{FRESH}/fresh_candidates.jsonl")]
    outp = f"{FRESH}/fresh_tasks.jsonl"
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            c = json.loads(l)["cand"]
            done.add((c["lib"], c["api"]))
    todo = [c for c in cands if (c["lib"], c["api"]) not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"候选 {len(cands)}, 本轮 {len(todo)}", flush=True)

    def one(c):
        old_env = env_name(c["lib"], c["old_ver"])
        new_env = env_name(c["lib"], c["new_ver"])
        env = GchamEnv(old_env, call_budget=a.budget, tars_dir=FTARS)
        try:
            sig, _ = split_sig(c.get("old_sig") or "?")
            got, _, _ = agent_loop(
                env, a.port, a.model, GEN_SYS,
                GEN_USER.format(lib=c["lib"], ver=c["old_ver"], api=c["api"],
                                sig=sig, budget=a.budget),
                "SNIPPET", a.budget + 5, max_tokens=1200)
            code = (got or {}).get("code")
            api_tail = c["api"].split(".")[-1]
            if not isinstance(code, str) or "assert" not in code \
                    or api_tail + "(" not in code.replace(" ", ""):
                return {"cand": c, "status": "no_snippet"}
            ok, err = env.run_py(code, timeout=60)
            if not ok:
                return {"cand": c, "status": "old_fail"}
            alines = assert_lines(code)
            if not alines or any(api_tail in l for l in alines):
                return {"cand": c, "status": "assert_not_freezable"}
        finally:
            env.cleanup()
        env2 = GchamEnv(new_env, tars_dir=FTARS)
        try:
            new_ok, new_err = env2.run_py(code, timeout=60)
        finally:
            env2.cleanup()
        if new_ok:
            return {"cand": c, "status": "still_passes"}
        if "ModuleNotFoundError" in new_err and c["lib"] not in new_err:
            return {"cand": c, "status": "env_false_positive"}
        return {"cand": c, "status": "task", "snippet": code,
                "new_err": new_err[:300], "old_env": old_env,
                "new_env": new_env}

    stat = collections.Counter()
    with open(outp, "a") as fh, cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(one, todo):
            stat[res["status"]] += 1
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            if sum(stat.values()) % 40 == 0:
                print(f"  [{sum(stat.values())}/{len(todo)}] {dict(stat)}",
                      flush=True)
    print(f"完成: {dict(stat)}")


def cmd_band(a):
    tasks = [json.loads(l) for l in open(f"{FRESH}/fresh_tasks.jsonl")
             if '"task"' in l]
    tasks = [t for t in tasks if t["status"] == "task"
             and t["cand"]["kind"] in a.kinds.split(",")]
    successors = {}
    if a.successors:
        raw = json.load(open(a.successors))
        successors = {tuple(k.split("|", 1)): v for k, v in raw.items()}
        tasks = [t for t in tasks
                 if (t["cand"]["lib"], t["cand"]["api"]) in successors]
        print(f"接替者判分: 限定 {len(tasks)} 题", flush=True)
    if a.limit:
        import random
        random.seed(7)
        tasks = random.sample(tasks, min(a.limit, len(tasks)))
    print(f"带宽测试: {len(tasks)} 题 x (none, static, fresh)", flush=True)

    # fresh 笔记: 每 (lib, api) 在新环境重生一次, 全条件共享
    fresh_notes = {}

    def manual_entry(c, t):
        """external 手册条目, 由世界(版本差分)给出, agent 只读不写"""
        what = "removed or renamed" if c["kind"] == "removed" \
            else "its signature changed"
        return (f"CHANGELOG {c['lib']} {c['old_ver']} -> {c['new_ver']}:\n"
                f"{c['api']} was {what} in this release.\n"
                f"Error when running the old usage now:\n{t['new_err'][:200]}")

    def make_fresh(t):
        c = t["cand"]
        key = (c["lib"], c["api"])
        seed = {"note_id": f"f_{c['lib']}_{c['api']}"[:80], "kind": "memory",
                "lib": c["lib"], "api": c["api"],
                "title": f"{c['api']} on {c['lib']}=={c['old_ver']}",
                "text": f"Validated usage of {c['api']} as of "
                        f"{c['lib']}=={c['old_ver']}.",
                "claims": [{"lib": c["lib"], "topic": c["api"],
                            "snippet": t["snippet"][:1200]}],
                "manual": manual_entry(c, t)}
        env = GchamEnv(t["new_env"], call_budget=a.repair_calls,
                       tars_dir=FTARS)
        try:
            class _A:
                port, model, repair_calls = a.port, a.model, a.repair_calls
            new, _ = rebirth_note(seed, env, _A)
        finally:
            env.cleanup()
        return key, seed, (new or seed)

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for key, seed, fresh in ex.map(make_fresh, tasks):
            fresh_notes[key] = (seed, fresh)
    n_changed = sum(1 for s, f in fresh_notes.values() if f is not s)
    # 笔记落盘, 论文审计用: static 看到的旧笔记与 oracle 看到的新鲜笔记
    with open(f"{FRESH}/band_notes.jsonl", "w") as nf:
        for (lib, api), (seed, fresh) in sorted(fresh_notes.items()):
            nf.write(json.dumps({"lib": lib, "api": api, "static_note": seed,
                                 "fresh_note": fresh,
                                 "rebirth_ok": fresh is not seed},
                                ensure_ascii=False) + "\n")
    print(f"重生笔记完成 {len(fresh_notes)} 条 (成功改写 {n_changed}), "
          f"已落盘 band_notes.jsonl", flush=True)

    def render(n):
        return ("\n## Your note on this library\n\n"
                f"{n['title']}\n{n['text']}\nsnippet:\n"
                f"{n['claims'][0]['snippet'][:400]}\n") if n else ""

    def exam(t, note):
        c = t["cand"]
        env = GchamEnv(t["new_env"], call_budget=a.task_budget,
                       tars_dir=FTARS)
        try:
            got, _, _ = agent_loop(
                env, a.port, a.model, EXAM_SYS,
                EXAM_USER.format(lib=c["lib"], new_ver=c["new_ver"],
                                 old_ver=c["old_ver"],
                                 snippet=t["snippet"][:1600],
                                 notes=render(note),
                                 budget=a.task_budget),
                "FIXED", a.task_budget + 5, max_tokens=1400)
            sub = (got or {}).get("code") or ""
            ok, err = grade(t, sub, env)
            key = (c["lib"], c["api"])
            if ok and successors.get(key):
                flat = sub.replace(" ", "")
                if not any(sc.split(".")[-1] + "(" in flat
                           or "." + sc.split(".")[-1] in flat
                           for sc in successors[key]):
                    ok, err = False, "successor not used"
            return ok, sub[:600], err
        finally:
            env.cleanup()

    out = open(f"{FRESH}/band_results.jsonl", "w")
    stat = collections.Counter()
    def run_all(t):
        key = (t["cand"]["lib"], t["cand"]["api"])
        seed, fresh = fresh_notes[key]
        r = {}
        for cond, note in (("none", None), ("static", seed), ("fresh", fresh)):
            ok, sub, err = exam(t, note)
            r[cond] = {"solved": ok, "submission": sub, "err": err[:150]}
        return t, r

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for t, r in ex.map(run_all, tasks):
            rec = {"task": t["cand"], "snippet": t["snippet"][:400]}
            rec.update({k: v for k, v in r.items()})
            out.write(json.dumps(rec) + "\n")
            out.flush()
            for cond in ("none", "static", "fresh"):
                stat[cond] += r[cond]["solved"]
            stat["n"] += 1
            if stat["n"] % 10 == 0:
                print(f"  [{stat['n']}] none {stat['none']} static "
                      f"{stat['static']} fresh {stat['fresh']}", flush=True)
    out.close()
    n = max(stat["n"], 1)
    print(f"\n带宽: none {stat['none']/n*100:.1f}%  static "
          f"{stat['static']/n*100:.1f}%  fresh(oracle) {stat['fresh']/n*100:.1f}%"
          f"  (n={n})")


MASK_ALL = """
# 掩码评测 v5: 三阶段整组手术.
# 0 抓取: 变动前逐载体取原方法; 1 隐藏: 删公开定义者的原名(乱码名接管);
# 2 查漏: 够不着乱码名的载体直接绑定, 还看得见原名的载体影子封印.
import importlib, gc, ctypes
def _mask_install(_targets, _mask):
    _tail = _targets[0].rsplit(".", 1)[1]
    _objs, _seen = [], set()
    for _t in _targets:
        try:
            _parts = _t.split(".")
            _o = importlib.import_module(_parts[0])
            for _i, _p in enumerate(_parts[1:-1], start=2):
                try:
                    _o = getattr(_o, _p)
                except AttributeError:
                    _o = importlib.import_module(".".join(_parts[:_i]))
        except Exception:
            continue
        if id(_o) not in _seen:
            _seen.add(id(_o))
            _objs.append(_o)
    def _own(_o):
        _d = getattr(_o, "__dict__", None)
        if _d is not None and not isinstance(_d, dict):
            _d = gc.get_referents(_d)[0]
        return _d
    def _flush(_o):
        if isinstance(_o, type):
            ctypes.pythonapi.PyType_Modified(ctypes.py_object(_o))
    _orig = dict((id(_o), getattr(_o, _tail, None)) for _o in _objs)
    for _o in _objs:
        _d = _own(_o)
        if _d is None or _tail not in _d:
            continue
        try:
            if _mask not in _d:
                _d[_mask] = _d[_tail]
            del _d[_tail]
            _flush(_o)
        except Exception:
            pass
    for _o in _objs:
        _d = _own(_o)
        if _d is None:
            continue
        try:
            try:
                getattr(_o, _mask)
                _has_mask = True
            except Exception:
                _has_mask = False
            if not _has_mask and _orig[id(_o)] is not None:
                _d[_mask] = _orig[id(_o)]
                _flush(_o)
            try:
                getattr(_o, _tail)
                _has_tail = True
            except Exception:
                _has_tail = False
            if _has_tail and _tail not in _d:
                def _raiser(self=None, *a, **k):
                    raise AttributeError(_tail + " was removed")
                _d[_tail] = property(_raiser)
                _flush(_o)
        except Exception:
            pass
try:
    _mask_install({targets!r}, {mask!r})
except Exception:
    pass
"""


def mask_script(targets, mask):
    return MASK_ALL.format(targets=list(targets), mask=mask)


def mask_name(api, style="hash", taken=None):
    import hashlib
    if style == "hash":
        return "_r" + hashlib.sha1(api.encode()).hexdigest()[:6]
    # soft: 像正经 API 的新名字, 校验不与现有名撞车
    tail = api.split(".")[-1]
    for cand in (f"{tail}2", f"{tail}_ex", f"{tail}_impl2",
                 f"{tail}_" + hashlib.sha1(api.encode()).hexdigest()[:3]):
        if not taken or cand not in taken:
            return cand
    return "_r" + hashlib.sha1(api.encode()).hexdigest()[:6]


def carrier_targets(fs, apis):
    """乱码名要装遍库里所有同名方法的载体 (数组/标量/别名类共享方法名)"""
    tail = fs.split(".")[-1]
    return [fs] + sorted([a1 for a1 in apis
                          if a1.split(".")[-1] == tail and a1 != fs],
                         key=len)[:200]


def write_mask(env, targets, mask):
    with open(os.path.join(env.workdir, "sitecustomize.py"), "w") as f:
        f.write(mask_script(targets, mask))


def cmd_masked(a):
    """掩码版三条件对照: 接替者被藏成乱码, 只有新鲜笔记知道新名字"""
    raw = json.load(open(a.successors or f"{FRESH}/successors_auto.json"))
    successors = {tuple(k.split("|", 1)): v for k, v in raw.items()}
    tasks = [json.loads(l) for l in open(f"{FRESH}/fresh_tasks.jsonl")
             if '"task"' in l]
    tasks = [t for t in tasks if t["status"] == "task"
             and t["cand"]["kind"] == "removed"
             and (t["cand"]["lib"], t["cand"]["api"]) in successors]
    # 接替者全名: 在新环境 API 清单里找 tail 对应的全路径
    def full_succ(t):
        c = t["cand"]
        tail = successors[(c["lib"], c["api"])][0]
        parent = ".".join(c["api"].split(".")[:-1])
        f = os.path.join(DUMPS, f"fresh_{c['lib']}_{c['new_ver']}_py3.12.json")
        apis = json.load(open(f)) if os.path.exists(f) else {}
        # 优先同父路径 (同类/同模块), 其次同类名, 最后最短路径
        if f"{parent}.{tail}" in apis:
            return f"{parent}.{tail}"
        cls = c["api"].split(".")[-2] if c["api"].count(".") >= 2 else ""
        same_cls = [a1 for a1 in apis if a1.split(".")[-1] == tail
                    and cls and a1.split(".")[-2:][0] == cls]
        if same_cls:
            return sorted(same_cls, key=len)[0]
        anym = [a1 for a1 in apis if a1.split(".")[-1] == tail]
        return sorted(anym, key=len)[0] if anym else None
    kept = []
    for t in tasks:
        fs = full_succ(t)
        if fs:
            c = t["cand"]
            f2 = os.path.join(DUMPS, f"fresh_{c['lib']}_{c['new_ver']}_py3.12.json")
            taken = {a1.split(".")[-1] for a1 in
                     (json.load(open(f2)) if os.path.exists(f2) else {})}
            t["succ_full"] = fs
            t["mask"] = mask_name(f"{c['lib']}.{fs.split('.')[-1]}",
                                  style=a.mask_style, taken=taken)
            t["targets"] = carrier_targets(
                fs, json.load(open(f2)) if os.path.exists(f2) else {})
            kept.append(t)
    tasks = kept
    if a.limit:
        tasks = tasks[:a.limit]
    print(f"掩码对照: {len(tasks)} 题", flush=True)

    EX_SYS = """You maintain a notebook about a Python library, inside the
CURRENT environment. An old API was removed; its replacement here is a
renamed symbol given below. Write a MINIMAL standalone example (2-5 lines)
that calls the replacement and asserts something about its result, so your
future self sees how to use it. Test it with bash first; one bash code block
per response, each command costs one unit of a small budget.

When it passes here, finish with:

EXAMPLE {"code": "<the runnable example>"}"""

    ex_cache = {}

    def usage_example(t):
        key = (t["cand"]["lib"], t["succ_full"], t["mask"])
        if key in ex_cache:
            return ex_cache[key]
        env = GchamEnv(t["new_env"], call_budget=6, tars_dir=FTARS)
        try:
            write_mask(env, t["targets"], t["mask"])
            for attempt in range(2):
                got, _, _ = agent_loop(
                    env, a.port, a.model, EX_SYS,
                    f"Library {t['cand']['lib']}. The removed API was "
                    f"{t['cand']['api']}; its replacement in this deployment "
                    f"is `{t['mask']}` (same call shape, on the same "
                    f"objects).\n\nFor reference, the OLD usage that worked "
                    f"before removal:\n```python\n{t['snippet'][:500]}\n```"
                    f"\n\n## Budget\n\n6 commands.\n\nWrite and test the "
                    f"example (it must call `{t['mask']}`), then hand in the "
                    f"EXAMPLE line.",
                    "EXAMPLE", 9, max_tokens=900)
                code = (got or {}).get("code") or ""
                if isinstance(code, str) and t["mask"] in code:
                    ok, _ = env.run_py(code, timeout=45)
                    if ok:
                        ex_cache[key] = code[:500]
                        break
            else:
                ex_cache[key] = ""
            ex_cache.setdefault(key, "")
        finally:
            env.cleanup()
        return ex_cache[key]

    def make_fresh_masked(t):
        c = t["cand"]
        m = t["mask"]
        if a.note_mode == "record":
            # 修复=改写笔记记录的用法本身: 旧片段换名, 乱码环境机器验证
            import re as _re
            old_tail = c["api"].split(".")[-1]
            ex = _re.sub(r"\b" + _re.escape(old_tail) + r"\b", m,
                         t["snippet"])
            env = GchamEnv(t["new_env"], tars_dir=FTARS)
            try:
                write_mask(env, t["targets"], t["mask"])
                ok, _ = env.run_py(ex, timeout=45)
            finally:
                env.cleanup()
            if not ok:
                ex = usage_example(t)
        else:
            ex = usage_example(t)
        parent = ".".join(t["succ_full"].split(".")[:-1])
        text = (f"{c['api']} was removed in {c['lib']} "
                f"{c['new_ver']}. In this deployment its replacement "
                f"is `{m}`: wherever the old code called "
                f".{c['api'].split('.')[-1]}(...), call .{m}(...) "
                f"with the same arguments instead. Do not use the public "
                f"replacement name you may remember: it does not exist "
                f"here. Verify yourself: hasattr({parent.split('.')[-1] or parent}, "
                f"'{m}') is True in this environment.")
        if ex:
            text += f"\nWorking example (verified here):\n{ex}"
        return {"note_id": f"m_{c['lib']}_{c['api']}"[:80], "kind": "memory",
                "lib": c["lib"], "api": c["api"],
                "title": f"{c['api']} migration ({c['lib']} {c['new_ver']})",
                "text": text,
                "claims": [{"lib": c["lib"], "topic": c["api"],
                            "snippet": ex}]}

    def exam(t, note):
        c = t["cand"]
        env = GchamEnv(t["new_env"], call_budget=a.task_budget,
                       tars_dir=FTARS)
        try:
            write_mask(env, t["targets"], t["mask"])
            got, _, _ = agent_loop(
                env, a.port, a.model, EXAM_SYS,
                EXAM_USER.format(lib=c["lib"], new_ver=c["new_ver"],
                                 old_ver=c["old_ver"],
                                 snippet=t["snippet"][:1600],
                                 notes=("\n## Your note on this library\n\n"
                                        + note["title"] + "\n" + note["text"]
                                        + "\n") if note else "",
                                 budget=a.task_budget),
                "FIXED", a.task_budget + 5, max_tokens=1400)
            sub = (got or {}).get("code") or ""
            ok, err = grade(t, sub, env)
            if ok and t["mask"] not in sub:
                ok, err = False, "masked successor not used"
            return ok, sub[:600], err
        finally:
            env.cleanup()

    seedify = lambda t: {"title": f"{t['cand']['api']} usage",
                         "text": f"Validated usage of {t['cand']['api']} as "
                                 f"of {t['cand']['lib']}=={t['cand']['old_ver']}.",
                         }
    out = open(f"{FRESH}/masked_results.jsonl", "w")
    stat = collections.Counter()

    def run_all(t):
        r = {}
        for cond in ("none", "static", "fresh"):
            note = None
            if cond == "static":
                note = seedify(t)
            elif cond == "fresh":
                note = make_fresh_masked(t)
            ok, sub, err = exam(t, note)
            r[cond] = {"solved": ok, "submission": sub, "err": err[:150]}
        return t, r

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for t, r in ex.map(run_all, tasks):
            rec = {"task": t["cand"], "mask": t["mask"],
                   "succ_full": t["succ_full"]}
            rec.update(r)
            out.write(json.dumps(rec) + "\n")
            out.flush()
            for cond in ("none", "static", "fresh"):
                stat[cond] += r[cond]["solved"]
            stat["n"] += 1
            if stat["n"] % 10 == 0:
                print(f"  [{stat['n']}] none {stat['none']} static "
                      f"{stat['static']} fresh {stat['fresh']}", flush=True)
    out.close()
    n = max(stat["n"], 1)
    print(f"\n掩码带宽: none {stat['none']/n*100:.1f}%  static "
          f"{stat['static']/n*100:.1f}%  fresh {stat['fresh']/n*100:.1f}%  (n={n})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["mine", "gen", "band", "masked"])
    ap.add_argument("--model", default="Qwen/Qwen3.5-9B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--budget", type=int, default=4)
    ap.add_argument("--task-budget", type=int, default=3)
    ap.add_argument("--repair-calls", type=int, default=8)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--successors", default="",
                    help="接替者名单 json; 给定则限定题目并要求提交用上接替者")
    ap.add_argument("--note-mode", default="guide",
                    choices=["guide", "record"],
                    help="guide=指路式小抄; record=修复改写记录的用法本身")
    ap.add_argument("--mask-style", default="hash", choices=["hash", "soft"],
                    help="掩码风格: hash=乱码(狠), soft=像正经API的新名(柔)")
    ap.add_argument("--kinds", default="removed",
                    help="band 只考这些类型, 默认只留硬题")
    a = ap.parse_args()
    {"mine": cmd_mine, "gen": cmd_gen, "band": cmd_band,
     "masked": cmd_masked}[a.cmd](a)


if __name__ == "__main__":
    main()
